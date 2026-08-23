"""Embedded workflow: fixed graph with human approval before simulation.

Graph shape is frozen for v0.15 (persisted checkpoints must stay
compatible)::

    HardwareAgent -> FirmwareAgent -> Approval(interrupt)
        -> Simulation -> DebugAgent -> Simulation (retry) | Finish

Nodes resolve their tools through the capability registry before use; a
missing capability routes to an explicit CAPABILITY_UNAVAILABLE failure
instead of an opaque error. A future release may replace this fixed graph
with Planner -> Capability Graph -> dynamic DAG without changing the node
contracts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from network_agent_rag.packs.embeddedops.agents.debug_agent import run_debug_agent
from network_agent_rag.packs.embeddedops.agents.firmware_agent import (
    GenerateCallback,
    run_firmware_agent,
)
from network_agent_rag.packs.embeddedops.agents.hardware_agent import (
    DesignCallback,
    RetrieveCallback,
    run_hardware_agent,
)
from network_agent_rag.packs.embeddedops.agents.state import EmbeddedInput, EmbeddedState
from network_agent_rag.artifact import ArtifactStore, ArtifactType
from network_agent_rag.audit import AuditEventType
from network_agent_rag.capability import CapabilityRegistry, CapabilityResolver
from network_agent_rag.packs.embeddedops.domain import (
    ApprovalAction,
    ApprovalRequest,
    FirmwareArtifact,
    HardwareDesign,
    SimulationTestCase,
    ValidationState,
)
from network_agent_rag.packs.embeddedops.simulation import SimulatorBackend
from network_agent_rag.policy import PolicyRiskLevel
from network_agent_rag.storage.base import AuditStore


def create_embedded_workflow(
    *,
    backend: SimulatorBackend,
    capability_registry: CapabilityRegistry,
    audit_log: AuditStore | None = None,
    artifact_store: ArtifactStore | None = None,
    design_hardware: DesignCallback | None = None,
    generate_firmware: GenerateCallback | None = None,
    retrieve_documents: RetrieveCallback | None = None,
    diagnose: Callable[[dict[str, Any]], Any] | None = None,
    test_cases: tuple[SimulationTestCase, ...] = (),
    max_fix_iterations: int = 3,
    approval_ttl_seconds: int = 1800,
    clock: Callable[[], datetime] | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Compile the embedded fixed graph."""

    if max_fix_iterations < 1:
        raise ValueError("max_fix_iterations must be at least 1")
    if approval_ttl_seconds <= 0:
        raise ValueError("approval_ttl_seconds must be positive")
    now = clock or (lambda: datetime.now(timezone.utc))
    resolver = CapabilityResolver(capability_registry)

    def _audit(
        *,
        action: str,
        outcome: str,
        details: dict[str, Any] | None = None,
        event_type: AuditEventType = AuditEventType.AGENT_CALL,
    ) -> None:
        if audit_log is not None:
            audit_log.record(
                incident_id="embedded",
                event_type=event_type,
                actor="EmbeddedWorkflow",
                action=action,
                outcome=outcome,
                details=details or {},
            )

    def initialize(raw_state: EmbeddedInput) -> dict[str, object]:
        goal = str(raw_state.get("goal", "")).strip()
        if not goal:
            raise ValueError("goal must be a non-empty string")
        task_id = str(raw_state.get("task_id") or f"emb-{uuid4().hex[:8]}")
        return {
            "task_id": task_id,
            "goal": goal,
            "iteration": 0,
            "hardware_design": None,
            "firmware": None,
            "approval_request": None,
            "approval_result": None,
            "compile_result": None,
            "simulation": None,
            "debug_report": None,
            "validation_state": ValidationState.CREATED.value,
            "error": None,
            "artifacts": [],
            "final_report": "",
        }

    def hardware_agent(state: EmbeddedState) -> dict[str, object]:
        if not resolver.resolve("mcu catalog lookup"):
            return _unavailable("mcu_catalog_lookup")
        update = run_hardware_agent(
            dict(state),
            design_hardware=design_hardware,
            retrieve_documents=retrieve_documents,
        )
        design = HardwareDesign.model_validate(update["hardware_design"])
        _persist(
            state,
            ArtifactType.HARDWARE_DESIGN,
            "hardware_design.json",
            design.model_dump_json(),
            "HardwareAgent",
        )
        _audit(
            action="hardware_design",
            outcome="succeeded",
            details={"mcu": design.mcu.model, "peripherals": len(design.peripherals)},
        )
        return update

    def firmware_agent(state: EmbeddedState) -> dict[str, object]:
        update = run_firmware_agent(dict(state), generate_firmware=generate_firmware)
        firmware = FirmwareArtifact.model_validate(update["firmware"])
        _persist(
            state,
            ArtifactType.FIRMWARE_SOURCE,
            firmware.filename,
            firmware.source,
            "FirmwareAgent",
        )
        _audit(
            action="firmware_generate",
            outcome="succeeded",
            details={"framework": firmware.framework.value},
        )
        return update

    def approval(state: EmbeddedState) -> dict[str, object]:
        firmware = FirmwareArtifact.model_validate(state["firmware"])
        request = ApprovalRequest(
            request_id=f"appr-{uuid4().hex[:8]}",
            action=ApprovalAction.SIMULATION_RUN,
            target=f"virtual:{getattr(backend, 'name', 'backend')}",
            risk_level=PolicyRiskLevel.LOW,
            artifact_ref=firmware.filename,
            execution_plan=(
                "compile firmware via esp32_compile capability",
                "flash to virtual device",
                "run simulation test cases",
                "verify assertions and telemetry",
            ),
            details={"task_id": state["task_id"]},
        ).with_created_at(now())
        _audit(
            action="approval_request",
            outcome="pending",
            details={"request_id": request.request_id, "action": request.action.value},
            event_type=AuditEventType.APPROVAL,
        )
        payload = interrupt({"approval_request": request.model_dump(mode="json")})
        decision = _validate_approval_response(payload, request, now(), approval_ttl_seconds)
        _audit(
            action="approval_decide",
            outcome=decision["decision"],
            details={"request_id": request.request_id, "actor": decision["actor"]},
            event_type=AuditEventType.APPROVAL,
        )
        return {
            "approval_request": request.model_dump(mode="json"),
            "approval_result": decision,
        }

    def simulation(state: EmbeddedState) -> dict[str, object]:
        if state.get("approval_result", {}).get("decision") != "approve":
            return _fail(state, "APPROVAL_REJECTED", "仿真前审批未通过，任务终止")
        if not resolver.resolve("esp32 compile firmware"):
            return _unavailable("esp32_compile")
        design = HardwareDesign.model_validate(state["hardware_design"])
        firmware = FirmwareArtifact.model_validate(state["firmware"])
        from network_agent_rag.packs.embeddedops.agents.validation_loop import (
            run_verification_pass,
        )

        outcome = run_verification_pass(
            backend, design=design, firmware=firmware, test_cases=test_cases
        )
        attempt = int(state.get("iteration", 0)) + 1
        _persist(
            state,
            ArtifactType.COMPILE_LOG,
            f"compile.attempt{attempt}.log",
            "\n".join(outcome.compile_result.log),
            "SimulationAgent",
        )
        if outcome.simulation is not None:
            _persist(
                state,
                ArtifactType.SIMULATION_LOG,
                f"simulation.attempt{attempt}.log",
                "\n".join(outcome.simulation.serial_log),
                "SimulationAgent",
            )
        _audit(
            action="verification_pass",
            outcome="passed" if outcome.state == ValidationState.PASSED else "failed",
            details={"state": outcome.state.value},
            event_type=AuditEventType.TOOL_CALL,
        )
        update: dict[str, object] = {
            "compile_result": {
                "success": outcome.compile_result.success,
                "log": list(outcome.compile_result.log),
                "errors": list(outcome.compile_result.errors),
                "binary_sha256": outcome.compile_result.binary_sha256,
            },
            "validation_state": outcome.state.value,
        }
        if outcome.simulation is not None:
            update["simulation"] = outcome.simulation.model_dump(mode="json")
            if outcome.simulation.error_category == "INFRASTRUCTURE_ERROR":
                update["error"] = (
                    "INFRASTRUCTURE_ERROR: "
                    f"{outcome.simulation.failure_reason or 'simulation infra failure'}"
                )
        return update

    def debug_agent(state: EmbeddedState) -> dict[str, object]:
        update = run_debug_agent(dict(state), diagnose=diagnose)
        iteration = int(state.get("iteration", 0)) + 1
        patched = update.get("firmware")
        retrying = patched is not None and iteration <= max_fix_iterations
        if retrying:
            update["iteration"] = iteration
            update["validation_state"] = ValidationState.RETRYING.value
            patched_artifact = FirmwareArtifact.model_validate(patched)
            _persist(
                state,
                ArtifactType.FIRMWARE_SOURCE,
                f"main.iter{iteration}.c",
                patched_artifact.source,
                "DebugAgent",
            )
        else:
            update["iteration"] = iteration - 1
            update["validation_state"] = ValidationState.FAILED.value
            update["error"] = (
                "MAX_FIX_ITERATIONS_REACHED"
                if patched is not None
                else "DEBUG_NO_PATCH_AVAILABLE"
            )
        _audit(
            action="debug",
            outcome="retry" if retrying else "failed",
            details={"iteration": iteration},
        )
        return update

    def finish(state: EmbeddedState) -> dict[str, object]:
        validation_state = ValidationState(state.get("validation_state", ValidationState.FAILED.value))
        if validation_state not in {ValidationState.PASSED, ValidationState.FAILED}:
            validation_state = ValidationState.FAILED
        report = (
            f"任务 {state['task_id']} 结束：{validation_state.value}，"
            f"修复迭代 {state.get('iteration', 0)} 轮。"
            + (f"错误：{state['error']}" if state.get("error") else "")
        )
        return {"validation_state": validation_state.value, "final_report": report}

    def _unavailable(capability: str) -> dict[str, object]:
        return _fail(
            cast(EmbeddedState, {}),
            "CAPABILITY_UNAVAILABLE",
            f"required capability is not registered/enabled: {capability}",
        )

    def _fail(state: EmbeddedState, code: str, message: str) -> dict[str, object]:
        return {
            "validation_state": ValidationState.FAILED.value,
            "error": f"{code}: {message}",
        }

    def _persist(
        state: EmbeddedState,
        artifact_type: ArtifactType,
        name: str,
        content: str,
        created_by: str,
    ) -> None:
        if artifact_store is None:
            return
        artifact_store.save(
            task_id=state["task_id"],
            type=artifact_type,
            name=name,
            version="1",
            created_by=created_by,
            content=content,
        )

    def route_after_simulation(state: EmbeddedState) -> str:
        validation_state = state.get("validation_state")
        if validation_state in {
            ValidationState.PASSED.value,
            ValidationState.FAILED.value,
        }:
            return "finish"
        simulation = state.get("simulation") or {}
        if simulation.get("error_category") == "INFRASTRUCTURE_ERROR":
            return "finish"
        return "debug"

    def route_after_debug(state: EmbeddedState) -> str:
        return "simulation" if state.get("validation_state") == ValidationState.RETRYING.value else "finish"

    builder = StateGraph(
        EmbeddedState, input_schema=EmbeddedInput, output_schema=EmbeddedState
    )
    builder.add_node("Initialize", initialize)
    builder.add_node("HardwareAgent", hardware_agent)
    builder.add_node("FirmwareAgent", firmware_agent)
    builder.add_node("Approval", approval)
    builder.add_node("Simulation", simulation)
    builder.add_node("DebugAgent", debug_agent)
    builder.add_node("Finish", finish)
    builder.add_edge(START, "Initialize")
    builder.add_edge("Initialize", "HardwareAgent")
    builder.add_edge("HardwareAgent", "FirmwareAgent")
    builder.add_edge("FirmwareAgent", "Approval")
    builder.add_edge("Approval", "Simulation")
    builder.add_conditional_edges(
        "Simulation",
        route_after_simulation,
        {"debug": "DebugAgent", "finish": "Finish"},
    )
    builder.add_conditional_edges(
        "DebugAgent",
        route_after_debug,
        {"simulation": "Simulation", "finish": "Finish"},
    )
    builder.add_edge("Finish", END)
    return builder.compile(checkpointer=checkpointer)


def _validate_approval_response(
    payload: object,
    request: ApprovalRequest,
    now: datetime,
    ttl_seconds: int,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("approval response must be a dictionary")
    decision = payload.get("decision")
    actor = payload.get("actor")
    if decision not in {"approve", "reject"}:
        raise ValueError("approval decision must be 'approve' or 'reject'")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("approval actor must be a non-empty string")
    created_at = request.created_at
    if created_at is not None:
        age = (now - created_at).total_seconds()
        if age > ttl_seconds:
            raise ValueError("approval request expired")
    return {
        "decision": decision,
        "actor": actor.strip(),
        "comment": str(payload.get("comment", "")),
        "request_id": request.request_id,
    }


__all__ = ["create_embedded_workflow"]
