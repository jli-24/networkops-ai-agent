"""Deterministic in-process simulator used for tests and offline demos.

No external toolchain is required. Behaviour is driven by plain markers so
tests can exercise every verification-error category:

- missing ``main``/``app_main`` or an ``#error`` directive -> compile error
- ``SIM_INFRA_TIMEOUT`` marker -> infrastructure error (no retry budget)
- test case ``expect_in_log`` matched against the generated serial log
"""

from __future__ import annotations

from uuid import uuid4

from network_agent_rag.artifact import compute_sha256
from network_agent_rag.packs.embeddedops.domain.models import (
    Framework,
    HardwareDesign,
    SimulationResult,
    SimulationTestCase,
    TestCaseResult,
    VerificationErrorCategory,
)
from network_agent_rag.packs.embeddedops.simulation.backend import (
    CompileResult,
    VirtualDevice,
)

_INFRA_TIMEOUT_MARKER = "SIM_INFRA_TIMEOUT"


class InProcessSimulatorBackend:
    name = "in_process"

    def __init__(self) -> None:
        self._logs: dict[str, tuple[str, ...]] = {}

    def compile_firmware(self, source: str, framework: Framework) -> CompileResult:
        lines = [f"[compile] framework={framework.value}"]
        errors: list[str] = []
        if "#error" in source:
            for line in source.splitlines():
                if "#error" in line:
                    errors.append(line.strip())
        if "int main" not in source and "void app_main" not in source:
            errors.append("undefined reference to `main`")
        if source.count("{") != source.count("}"):
            errors.append("syntax error: unbalanced braces")
        if errors:
            return CompileResult(
                success=False,
                log=tuple([*lines, *[f"[error] {item}" for item in errors]]),
                errors=tuple(errors),
            )
        binary_digest = compute_sha256(f"BIN:{source}")
        return CompileResult(
            success=True,
            log=(f"[compile] OK binary_sha256={binary_digest[:16]}…",),
            binary_sha256=binary_digest,
        )

    def create_virtual_device(self, design: HardwareDesign) -> VirtualDevice:
        return VirtualDevice(
            device_id=f"vdev-{uuid4().hex[:8]}",
            mcu_model=design.mcu.model,
            peripherals=tuple(peripheral.name for peripheral in design.peripherals),
        )

    def run_simulation(
        self,
        device: VirtualDevice,
        firmware_source: str,
        test_cases: tuple[SimulationTestCase, ...],
    ) -> SimulationResult:
        if _INFRA_TIMEOUT_MARKER in firmware_source:
            result = SimulationResult(
                passed=False,
                error_category=VerificationErrorCategory.INFRASTRUCTURE_ERROR,
                failure_reason="simulation timed out (infrastructure)",
            )
            self._logs[device.device_id] = ("[sim] infrastructure timeout",)
            return result
        log = [
            f"[boot] virtual {device.mcu_model} device_id={device.device_id}",
            *[
                f"[peripheral:{name}] initialized"
                for name in device.peripherals
            ],
            "[done] simulation complete",
        ]
        self._logs[device.device_id] = tuple(log)
        joined = "\n".join(log)
        results = tuple(
            TestCaseResult(
                name=case.name,
                passed=case.expect_in_log is None or case.expect_in_log in joined,
                detail=(
                    "no assertion"
                    if case.expect_in_log is None
                    else f"expect '{case.expect_in_log}'"
                ),
            )
            for case in test_cases
        )
        failed = [item for item in results if not item.passed]
        if failed:
            return SimulationResult(
                passed=False,
                error_category=VerificationErrorCategory.SIMULATION_FAILURE,
                serial_log=tuple(log),
                test_results=results,
                failure_reason="; ".join(item.name for item in failed),
            )
        return SimulationResult(
            passed=True,
            serial_log=tuple(log),
            test_results=results,
        )

    def read_serial_log(self, device: VirtualDevice) -> tuple[str, ...]:
        return self._logs.get(device.device_id, ())


__all__ = ["InProcessSimulatorBackend"]
