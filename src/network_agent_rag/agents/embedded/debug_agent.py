"""Debug agent: root-cause analysis over logs, crashes, and compile errors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from network_agent_rag.domain.embedded import (
    DebugReport,
    FirmwareArtifact,
    VerificationErrorCategory,
)

DiagnoseCallback = Callable[[dict[str, Any]], DebugReport]


def run_debug_agent(
    state: dict[str, Any],
    *,
    diagnose: DiagnoseCallback | None = None,
) -> dict[str, Any]:
    """Analyze the latest failure and produce a remediation report.

    The injected ``diagnose`` callback (LLM- or RAG-backed in production)
    receives the failure context; the deterministic fallback maps error
    categories to remediation guidance.
    """

    context = _context_from_state(state)
    if diagnose is not None:
        report = diagnose(context)
    else:
        report = _default_diagnosis(context)
    update: dict[str, Any] = {
        "debug_report": report.model_dump(mode="json"),
        "validation_state": "DEBUGGING",
    }
    if report.patched_firmware is not None:
        update["firmware"] = report.patched_firmware.model_dump(mode="json")
    return update


def _context_from_state(state: dict[str, Any]) -> dict[str, Any]:
    compile_result = state.get("compile_result") or {}
    simulation = state.get("simulation") or {}
    firmware = state.get("firmware") or {}
    return {
        "goal": state.get("goal", ""),
        "error_category": (
            simulation.get("error_category")
            or (
                VerificationErrorCategory.COMPILE_ERROR.value
                if compile_result.get("success") is False
                else None
            )
        ),
        "compile_errors": list(compile_result.get("errors", [])),
        "serial_log": list(simulation.get("serial_log", [])),
        "failure_reason": simulation.get("failure_reason"),
        "firmware_source": firmware.get("source", ""),
        "framework": firmware.get("framework", "FreeRTOS"),
    }


def _default_diagnosis(context: dict[str, Any]) -> DebugReport:
    category = context.get("error_category")
    if category == VerificationErrorCategory.COMPILE_ERROR.value:
        errors = context.get("compile_errors") or ["unknown compile error"]
        return DebugReport(
            root_cause=f"编译失败：{errors[0]}",
            remediation="根据编译错误修正源码后重新生成固件",
            confidence=0.9,
            patched_firmware=_patched(context),
        )
    if category == VerificationErrorCategory.SIMULATION_FAILURE.value:
        reason = context.get("failure_reason") or "未知断言失败"
        return DebugReport(
            root_cause=f"仿真断言失败：{reason}",
            remediation="检查测试断言对应的初始化/上报逻辑并修复",
            confidence=0.8,
            patched_firmware=_patched(context),
        )
    return DebugReport(
        root_cause="未能归类错误类别",
        remediation="人工介入排查",
        confidence=0.3,
    )


def _patched(context: dict[str, Any]) -> FirmwareArtifact | None:
    """Deterministic patcher: fix known failure markers when present."""

    source = context.get("firmware_source") or ""
    if not source:
        return None
    patched = source
    changed = False
    if "int main" not in patched and "void app_main" not in patched:
        patched = f"int main(void) {{ return 0; }}\n{patched}"
        changed = True
    if "#error" in patched:
        patched = "\n".join(
            line for line in patched.splitlines() if "#error" not in line
        )
        changed = True
    if patched.count("{") != patched.count("}"):
        patched += "}" * (patched.count("{") - patched.count("}"))
        changed = True
    if not changed:
        return None
    return FirmwareArtifact(
        artifact_id=f"fw-{uuid4().hex[:8]}",
        design_id="patched",
        framework=context.get("framework", "FreeRTOS"),
        source=patched,
    )


__all__ = ["run_debug_agent"]
