"""Shared state and typed contracts for the multi-agent workflow."""

from __future__ import annotations

from typing import Literal
from typing_extensions import TypedDict

from network_agent_rag.packs.networkops.agents.diagnosis_workflow import (
    DiagnosisPlan,
    RootCauseHypothesis,
)
from network_agent_rag.packs.networkops.agents.workflow import AgentState


AgentName = Literal["topology", "logs", "diagnosis", "repair", "report"]
RiskLevel = Literal["low", "medium", "high", "critical"]


class SupervisorPlan(TypedDict):
    analysis: DiagnosisPlan
    required_agents: list[AgentName]
    requires_repair: bool


class DiagnosisResult(TypedDict):
    summary: str
    hypotheses: list[RootCauseHypothesis]
    evidence_refs: list[str]
    remaining_uncertainties: list[str]


class RepairPlan(TypedDict):
    risk_level: RiskLevel
    requires_human_approval: bool
    steps: list[str]
    verification_steps: list[str]
    rollback_conditions: list[str]
    execution_status: Literal["not_executed"]


class MultiAgentState(AgentState):
    incident_id: str
    task_plan: SupervisorPlan
    pending_agents: list[AgentName]
    completed_agents: list[AgentName]
    current_agent: AgentName | None
    device_evidence: dict[str, object]
    log_evidence: list[dict[str, object]]
    diagnosis_result: DiagnosisResult | None
    repair_plan: RepairPlan | None
    report: str
    agent_errors: dict[str, str]
    handoff_count: int
    diagnosis_iteration: int
    report_iteration: int
    report_feedback: str | None


def completion_update(
    state: MultiAgentState,
    agent: AgentName,
) -> dict[str, object]:
    """Return the common state update after one agent finishes."""

    return {
        "pending_agents": [name for name in state["pending_agents"] if name != agent],
        "completed_agents": [*state["completed_agents"], agent],
        "current_agent": None,
        "handoff_count": state["handoff_count"] + 1,
    }


def validate_repair_plan(plan: object) -> None:
    if not isinstance(plan, dict):
        raise ValueError("repair plan must be a dictionary")
    if plan.get("risk_level") not in {"low", "medium", "high", "critical"}:
        raise ValueError("repair plan risk_level is invalid")
    if not isinstance(plan.get("requires_human_approval"), bool):
        raise ValueError("repair plan requires_human_approval must be a bool")
    if plan.get("execution_status") != "not_executed":
        raise ValueError("repair plan execution_status must be not_executed")
    for field in ("steps", "verification_steps", "rollback_conditions"):
        value = plan.get(field)
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"repair plan {field} must contain non-empty strings")
    unsafe = ("已自动执行", "自动执行修复", "已重启", "已修改配置", "已更换")
    if any(term in step for step in plan["steps"] for term in unsafe):
        raise ValueError("repair plan must not claim execution")


__all__ = [
    "AgentName",
    "DiagnosisResult",
    "MultiAgentState",
    "RepairPlan",
    "RiskLevel",
    "SupervisorPlan",
    "validate_repair_plan",
]
