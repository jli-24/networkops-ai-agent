"""Read-only compliance-report projection services."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from network_agent_rag.audit import AuditEvent, AuditEventType
from network_agent_rag.governance.events import SecurityEventCenter
from network_agent_rag.governance.models import (
    ComplianceEvidence,
    ComplianceReport,
    ComplianceReportType,
    ComplianceRiskSummary,
    GovernanceRiskLevel,
)
from network_agent_rag.observability import IncidentTimelineBuilder
from network_agent_rag.storage.base import AuditStore, TraceStore


class ComplianceReportService:
    def __init__(
        self,
        audit_store: AuditStore,
        trace_store: TraceStore,
        *,
        clock=lambda: datetime.now(timezone.utc),
        id_factory=lambda: uuid4().hex,
    ) -> None:
        self.audit_store = audit_store
        self.trace_store = trace_store
        self.clock = clock
        self.id_factory = id_factory
        self.event_center = SecurityEventCenter(audit_store, trace_store)

    def build(
        self,
        incident_id: str,
        report_type: ComplianceReportType,
    ) -> ComplianceReport:
        audit_events = self.audit_store.list_events(incident_id)
        evidence = tuple(_evidence(event) for event in audit_events)
        approvals = tuple(
            item
            for event, item in zip(audit_events, evidence)
            if event.event_type == AuditEventType.APPROVAL
            or "approval" in event.action
            or event.action in {"approve", "reject"}
        )
        execution = tuple(
            item
            for event, item in zip(audit_events, evidence)
            if event.event_type == AuditEventType.TOOL_CALL and event.actor == "Execute"
        )
        verification = tuple(
            item
            for event, item in zip(audit_events, evidence)
            if "verify" in event.action.lower() or "verification" in event.action.lower()
        )
        actors = tuple(dict.fromkeys(event.actor_id or event.actor for event in audit_events))
        risk_summary = _risk_summary(audit_events)
        missing = []
        if not verification:
            missing.append("verification")
        if risk_summary.latest_level is None:
            missing.append("risk_assessment")
        return ComplianceReport(
            report_id=self.id_factory(),
            report_type=report_type,
            incident_id=incident_id,
            generated_at=self.clock(),
            timeline=tuple(
                IncidentTimelineBuilder(self.trace_store, self.audit_store).build(
                    incident_id
                )
            ),
            actors=actors,
            actions=evidence,
            approvals=approvals,
            execution=execution,
            verification=verification,
            security_events=self.event_center.query(incident_id=incident_id),
            risk_summary=risk_summary,
            missing_evidence=tuple(missing),
        )


def _evidence(event: AuditEvent) -> ComplianceEvidence:
    return ComplianceEvidence(
        source_event_id=event.event_id,
        actor_id=event.actor_id or event.actor,
        action=event.action,
        outcome=event.outcome,
        timestamp=event.created_at,
    )


def _risk_summary(events: list[AuditEvent]) -> ComplianceRiskSummary:
    assessments = [event for event in events if event.action == "risk_assessment_created"]
    latest_score: int | None = None
    latest_level: GovernanceRiskLevel | None = None
    if assessments:
        score = assessments[-1].details.get("risk_score")
        level = assessments[-1].details.get("risk_level")
        if isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100:
            latest_score = score
        if level in {item.value for item in GovernanceRiskLevel}:
            latest_level = GovernanceRiskLevel(level)
    else:
        legacy = [event for event in events if event.action == "evaluate_risk"]
        if legacy:
            level = legacy[-1].details.get("risk_level")
            if level in {item.value for item in GovernanceRiskLevel}:
                latest_level = GovernanceRiskLevel(level)
    return ComplianceRiskSummary(
        assessment_count=len(assessments),
        latest_score=latest_score,
        latest_level=latest_level,
    )


__all__ = ["ComplianceReportService"]
