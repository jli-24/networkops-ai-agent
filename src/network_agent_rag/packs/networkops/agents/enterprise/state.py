"""State contracts for the checkpointed enterprise workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing_extensions import NotRequired, TypedDict

from network_agent_rag.packs.networkops.agents.multi_agent.state import MultiAgentState, RiskLevel


class RepairAction(TypedDict):
    action_id: str
    tool_name: str
    target: str
    arguments: dict[str, object]
    verification_steps: list[str]
    rollback_instructions: str


class RiskDecision(TypedDict):
    risk_level: RiskLevel
    approval_required: bool
    reasons: list[str]
    plan_digest: str
    requested_at: datetime
    expires_at: datetime


class ApprovalResult(TypedDict):
    decision: Literal["approve", "reject"]
    actor: str
    plan_digest: str
    comment: str | None
    decided_at: datetime


class ActionResult(TypedDict):
    action_id: str
    status: Literal["succeeded", "failed"]
    message: str


class ExecutionResult(TypedDict):
    status: Literal["succeeded", "failed", "blocked", "not_executed"]
    actions: list[ActionResult]
    error_code: str | None
    message: str


class EnterpriseState(MultiAgentState):
    session_id: NotRequired[str]
    proposed_actions: list[RepairAction]
    risk_decision: RiskDecision | None
    approval_result: ApprovalResult | None
    execution_result: ExecutionResult | None
    enterprise_status: Literal[
        "running",
        "pending_approval",
        "approved",
        "rejected",
        "executed",
        "failed",
    ]


__all__ = [
    "ActionResult",
    "ApprovalResult",
    "EnterpriseState",
    "ExecutionResult",
    "RepairAction",
    "RiskDecision",
]
