"""Deterministic governance risk scoring without workflow side effects."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from network_agent_rag.governance.models import (
    GovernanceOperation,
    GovernanceRiskLevel,
    RiskAssessment,
    RiskRequest,
)


_BASE_SCORES = {
    GovernanceOperation.VIEW_DEVICE_STATUS: 10,
    GovernanceOperation.CREATE_REPAIR_PLAN: 35,
    GovernanceOperation.RESTART_DEVICE: 60,
    GovernanceOperation.BATCH_CONFIGURATION_CHANGE: 80,
}


class RiskScorer:
    def assess(
        self,
        request: RiskRequest,
        *,
        authorization_denied_count: int = 0,
        repair_failed_count: int = 0,
        assessment_id: str | None = None,
        now: datetime | None = None,
    ) -> RiskAssessment:
        if authorization_denied_count < 0 or repair_failed_count < 0:
            raise ValueError("history counts must be non-negative")
        base = _BASE_SCORES[request.operation]
        reasons = [f"operation_base:{request.operation.value}={base}"]
        device_modifier = 20 if len(request.devices) >= 6 else 10 if len(request.devices) >= 2 else 0
        authorization_modifier = min(15, authorization_denied_count * 5)
        repair_modifier = min(15, repair_failed_count * 5)
        for label, modifier in (
            ("device_scope", device_modifier),
            ("authorization_history", authorization_modifier),
            ("repair_failure_history", repair_modifier),
        ):
            if modifier:
                reasons.append(f"{label}:+{modifier}")
        score = min(100, base + device_modifier + authorization_modifier + repair_modifier)
        return RiskAssessment(
            assessment_id=assessment_id or uuid4().hex,
            incident_id=request.incident_id,
            actor_id=request.identity.user_id,
            risk_score=score,
            risk_level=_level(score),
            reasons=tuple(reasons),
            created_at=now or datetime.now(timezone.utc),
        )


def _level(score: int) -> GovernanceRiskLevel:
    if score >= 75:
        return GovernanceRiskLevel.CRITICAL
    if score >= 50:
        return GovernanceRiskLevel.HIGH
    if score >= 25:
        return GovernanceRiskLevel.MEDIUM
    return GovernanceRiskLevel.LOW


__all__ = ["RiskScorer"]
