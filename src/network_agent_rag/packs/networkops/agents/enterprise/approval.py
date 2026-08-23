"""Risk classification and approval validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from network_agent_rag.packs.networkops.agents.enterprise.state import (
    ApprovalResult,
    EnterpriseState,
    RepairAction,
    RiskDecision,
)


class ApprovalConflict(ValueError):
    """Raised when an approval cannot be applied to the pending plan."""


def plan_digest(actions: list[RepairAction] | list[dict[str, object]]) -> str:
    encoded = json.dumps(
        actions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def evaluate_risk(
    state: EnterpriseState | dict[str, object],
    *,
    now: datetime | None = None,
    approval_ttl_seconds: int = 1800,
) -> RiskDecision:
    if approval_ttl_seconds <= 0:
        raise ValueError("approval_ttl_seconds must be positive")
    plan = state.get("repair_plan")
    if not isinstance(plan, dict):
        raise ValueError("repair_plan is required for risk evaluation")
    risk_level = plan.get("risk_level")
    if risk_level not in {"low", "medium", "high", "critical"}:
        raise ValueError("repair_plan risk_level is invalid")
    forced = plan.get("requires_human_approval")
    if not isinstance(forced, bool):
        raise ValueError("repair_plan requires_human_approval must be a bool")
    actions = state.get("proposed_actions")
    if not isinstance(actions, list):
        raise ValueError("proposed_actions must be a list")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    required = risk_level in {"high", "critical"} or forced
    reasons = []
    if risk_level in {"high", "critical"}:
        reasons.append(f"risk level is {risk_level}")
    if forced:
        reasons.append("repair plan requires human approval")
    return {
        "risk_level": risk_level,
        "approval_required": required,
        "reasons": reasons,
        "plan_digest": plan_digest(actions),
        "requested_at": current,
        "expires_at": current + timedelta(seconds=approval_ttl_seconds),
    }


def validate_approval(
    state: EnterpriseState | dict[str, object],
    payload: dict[str, object],
    *,
    now: datetime | None = None,
) -> ApprovalResult:
    if state.get("approval_result") is not None:
        raise ApprovalConflict("approval has already been decided")
    risk = state.get("risk_decision")
    if not isinstance(risk, dict):
        raise ApprovalConflict("incident has no pending risk decision")
    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    digest = payload.get("plan_digest")
    if digest != risk.get("plan_digest"):
        raise ApprovalConflict("approval plan digest does not match")
    current = now or datetime.now(timezone.utc)
    expires_at = risk.get("expires_at")
    if not isinstance(expires_at, datetime):
        raise ApprovalConflict("approval expiry is unavailable")
    if decision == "approve" and current > expires_at:
        raise ApprovalConflict("approval request has expired")
    comment = payload.get("comment")
    if comment is not None and (not isinstance(comment, str) or not comment.strip()):
        raise ValueError("comment must be non-empty when provided")
    return {
        "decision": decision,
        "actor": actor.strip(),
        "plan_digest": str(digest),
        "comment": comment.strip() if isinstance(comment, str) else None,
        "decided_at": current,
    }


__all__ = [
    "ApprovalConflict",
    "evaluate_risk",
    "plan_digest",
    "validate_approval",
]
