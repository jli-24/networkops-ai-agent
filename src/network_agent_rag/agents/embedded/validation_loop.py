"""Verification closed loop as an explicit state machine.

Error categories route differently:

- COMPILE_ERROR / SIMULATION_FAILURE -> Debug Agent, consuming retry budget
- INFRASTRUCTURE_ERROR -> does NOT consume retry budget; terminates the
  loop (the environment, not the code, is at fault)

Every transition is validated against the table below, so checkpoints and
dashboards can trust ``validation_state`` at all times.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from network_agent_rag.artifact import ArtifactStore, ArtifactType
from network_agent_rag.domain.embedded import (
    DebugReport,
    FirmwareArtifact,
    HardwareDesign,
    SimulationResult,
    SimulationTestCase,
    ValidationState,
    VerificationErrorCategory,
    VerificationReport,
)
from network_agent_rag.infrastructure.simulation import (
    CompileResult,
    SimulatorBackend,
)


class InvalidStateTransition(ValueError):
    """Raised when a transition is not allowed by the state table."""


_TRANSITIONS: dict[ValidationState, frozenset[ValidationState]] = {
    ValidationState.CREATED: frozenset({ValidationState.GENERATED}),
    ValidationState.GENERATED: frozenset({ValidationState.COMPILE_RUNNING}),
    ValidationState.COMPILE_RUNNING: frozenset(
        {ValidationState.COMPILE_FAILED, ValidationState.SIMULATION_RUNNING}
    ),
    ValidationState.COMPILE_FAILED: frozenset({ValidationState.DEBUGGING}),
    ValidationState.SIMULATION_RUNNING: frozenset(
        {ValidationState.SIMULATION_FAILED, ValidationState.PASSED}
    ),
    ValidationState.SIMULATION_FAILED: frozenset(
        {ValidationState.DEBUGGING, ValidationState.FAILED}
    ),
    ValidationState.DEBUGGING: frozenset(
        {ValidationState.RETRYING, ValidationState.FAILED}
    ),
    ValidationState.RETRYING: frozenset({ValidationState.COMPILE_RUNNING}),
    ValidationState.PASSED: frozenset(),
    ValidationState.FAILED: frozenset(),
}


def assert_transition(current: ValidationState, target: ValidationState) -> None:
    if target not in _TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"illegal transition {current.value} -> {target.value}"
        )


@dataclass
class ValidationStateMachine:
    current: ValidationState = ValidationState.CREATED

    def transition(self, target: ValidationState) -> ValidationState:
        assert_transition(self.current, target)
        self.current = target
        return self.current


TransitionObserver = Callable[[ValidationState, ValidationState], None]


@dataclass(frozen=True)
class VerificationPass:
    """One compile+simulate pass over a firmware candidate."""

    compile_result: CompileResult
    simulation: SimulationResult | None
    state: ValidationState


def run_verification_pass(
    backend: SimulatorBackend,
    *,
    design: HardwareDesign,
    firmware: FirmwareArtifact,
    test_cases: tuple[SimulationTestCase, ...] = (),
    machine: ValidationStateMachine | None = None,
) -> VerificationPass:
    """Compile then (if compilation succeeded) simulate one candidate."""

    machine = machine or ValidationStateMachine(ValidationState.GENERATED)
    machine.transition(ValidationState.COMPILE_RUNNING)
    compiled = backend.compile_firmware(firmware.source, firmware.framework)
    if not compiled.success:
        machine.transition(ValidationState.COMPILE_FAILED)
        return VerificationPass(
            compile_result=compiled, simulation=None, state=machine.current
        )
    machine.transition(ValidationState.SIMULATION_RUNNING)
    device = backend.create_virtual_device(design)
    simulation = backend.run_simulation(device, firmware.source, test_cases)
    machine.transition(
        ValidationState.PASSED if simulation.passed else ValidationState.SIMULATION_FAILED
    )
    return VerificationPass(
        compile_result=compiled, simulation=simulation, state=machine.current
    )


DiagnoseCallback = Callable[[dict[str, Any]], DebugReport]


class ValidationLoop:
    """Autonomous compile -> simulate -> debug -> retry loop."""

    def __init__(
        self,
        backend: SimulatorBackend,
        *,
        max_fix_iterations: int = 3,
        diagnose: DiagnoseCallback | None = None,
        artifact_store: ArtifactStore | None = None,
        on_transition: TransitionObserver | None = None,
    ) -> None:
        if max_fix_iterations < 1:
            raise ValueError("max_fix_iterations must be at least 1")
        self.backend = backend
        self.max_fix_iterations = max_fix_iterations
        self.diagnose = diagnose
        self.artifact_store = artifact_store
        self.on_transition = on_transition

    def run(
        self,
        *,
        task_id: str,
        design: HardwareDesign,
        firmware: FirmwareArtifact,
        test_cases: tuple[SimulationTestCase, ...] = (),
    ) -> VerificationReport:
        machine = ValidationStateMachine(ValidationState.GENERATED)
        iterations = 0
        attempt = 0
        current = firmware
        root_cause: str | None = None
        error_category: VerificationErrorCategory | None = None
        artifact_ids: list[str] = []

        while True:
            attempt += 1
            self._advance(machine, ValidationState.COMPILE_RUNNING)
            compiled = self.backend.compile_firmware(current.source, current.framework)
            artifact_ids += self._save(
                task_id,
                ArtifactType.COMPILE_LOG,
                f"compile.attempt{attempt}.log",
                "\n".join(compiled.log),
            )
            if not compiled.success:
                self._advance(machine, ValidationState.COMPILE_FAILED)
                error_category = VerificationErrorCategory.COMPILE_ERROR
                patched, iterations, root_cause = self._debug_cycle(
                    machine=machine,
                    design=design,
                    compile_result=compiled,
                    simulation=None,
                    firmware=current,
                    iterations=iterations,
                )
                if patched is None:
                    return self._report(
                        task_id, machine, iterations, error_category, root_cause, artifact_ids
                    )
                current = patched
                continue

            self._advance(machine, ValidationState.SIMULATION_RUNNING)
            device = self.backend.create_virtual_device(design)
            simulation = self.backend.run_simulation(device, current.source, test_cases)
            artifact_ids += self._save(
                task_id,
                ArtifactType.SIMULATION_LOG,
                f"simulation.attempt{attempt}.log",
                "\n".join(simulation.serial_log),
            )
            if simulation.passed:
                self._advance(machine, ValidationState.PASSED)
                return self._report(task_id, machine, iterations, None, None, artifact_ids)

            self._advance(machine, ValidationState.SIMULATION_FAILED)
            error_category = simulation.error_category
            if error_category == VerificationErrorCategory.INFRASTRUCTURE_ERROR:
                # Environmental failure: never burns the fix-retry budget.
                self._advance(machine, ValidationState.FAILED)
                return self._report(
                    task_id,
                    machine,
                    iterations,
                    error_category,
                    simulation.failure_reason,
                    artifact_ids,
                )
            patched, iterations, root_cause = self._debug_cycle(
                machine=machine,
                design=design,
                compile_result=compiled,
                simulation=simulation,
                firmware=current,
                iterations=iterations,
            )
            if patched is None:
                return self._report(
                    task_id, machine, iterations, error_category, root_cause, artifact_ids
                )
            current = patched

    def _debug_cycle(
        self,
        *,
        machine: ValidationStateMachine,
        design: HardwareDesign,
        compile_result: CompileResult,
        simulation: SimulationResult | None,
        firmware: FirmwareArtifact,
        iterations: int,
    ) -> tuple[FirmwareArtifact | None, int, str]:
        """Consult the debug agent; schedule a retry or accept failure.

        Returns ``(patched_firmware | None, iterations, root_cause)``; a
        None firmware means the loop terminated in FAILED.
        """

        from network_agent_rag.agents.embedded.debug_agent import run_debug_agent

        self._advance(machine, ValidationState.DEBUGGING)
        update = run_debug_agent(
            {
                "goal": design.goal,
                "compile_result": {
                    "success": compile_result.success,
                    "errors": list(compile_result.errors),
                },
                "simulation": (
                    simulation.model_dump(mode="json")
                    if simulation is not None
                    else None
                ),
                "firmware": firmware.model_dump(mode="json"),
            },
            diagnose=self.diagnose,
        )
        report = DebugReport.model_validate(update["debug_report"])
        patched = update.get("firmware")
        if patched is None or iterations >= self.max_fix_iterations:
            self._advance(machine, ValidationState.FAILED)
            return None, iterations, report.root_cause
        iterations += 1
        self._advance(machine, ValidationState.RETRYING)
        return FirmwareArtifact.model_validate(patched), iterations, report.root_cause

    def _advance(
        self, machine: ValidationStateMachine, target: ValidationState
    ) -> None:
        previous = machine.current
        machine.transition(target)
        if self.on_transition is not None:
            self.on_transition(previous, target)

    def _report(
        self,
        task_id: str,
        machine: ValidationStateMachine,
        iterations: int,
        error_category: VerificationErrorCategory | None,
        root_cause: str | None,
        artifact_ids: list[str],
    ) -> VerificationReport:
        if machine.current == ValidationState.PASSED:
            summary = f"验证通过（{iterations} 次修复迭代）"
        elif error_category == VerificationErrorCategory.INFRASTRUCTURE_ERROR:
            summary = "基础设施错误终止验证（未消耗修复重试次数）"
        else:
            summary = f"验证失败：{root_cause or '未知原因'}"
        report = VerificationReport(
            task_id=task_id,
            final_state=machine.current,
            iterations=iterations,
            error_category=error_category,
            root_cause=root_cause,
            summary=summary,
            artifact_ids=tuple(artifact_ids),
        )
        self._save(
            task_id, ArtifactType.REPORT, "report.json", report.model_dump_json()
        )
        return report

    def _save(
        self,
        task_id: str,
        artifact_type: ArtifactType,
        name: str,
        content: str,
    ) -> list[str]:
        if self.artifact_store is None:
            return []
        artifact = self.artifact_store.save(
            task_id=task_id,
            type=artifact_type,
            name=name,
            version="1",
            created_by="ValidationLoop",
            content=content,
        )
        return [artifact.artifact_id]


__all__ = [
    "InvalidStateTransition",
    "ValidationLoop",
    "ValidationStateMachine",
    "assert_transition",
    "run_verification_pass",
]
