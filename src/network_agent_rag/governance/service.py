"""Governance facade with three explicit append-only Audit actions."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from network_agent_rag.audit import AuditEventType
from network_agent_rag.governance.compliance import ComplianceReportService
from network_agent_rag.governance.events import SecurityEventCenter
from network_agent_rag.governance.models import (
    ComplianceReport,
    ComplianceReportType,
    GovernanceMetricsSnapshot,
    GovernanceRiskLevel,
    GovernanceSeverity,
    RiskAssessment,
    RiskRequest,
    SecurityEvent,
    SecurityEventType,
)
from network_agent_rag.governance.risk import RiskScorer
from network_agent_rag.observability.governance import (
    GovernanceDecision,
    GovernanceQuery,
)
from network_agent_rag.storage.base import AuditStore, TraceStore


class GovernanceService:
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
        self.risk_scorer = RiskScorer()

    def query_events(
        self,
        *,
        incident_id: str | None = None,
        actor_id: str | None = None,
        event_type: SecurityEventType | None = None,
        severity: GovernanceSeverity | None = None,
    ) -> tuple[SecurityEvent, ...]:
        return self.event_center.query(
            incident_id=incident_id,
            actor_id=actor_id,
            event_type=event_type,
            severity=severity,
        )

    def sync_security_events(
        self, incident_id: str | None = None
    ) -> tuple[SecurityEvent, ...]:
        return self.event_center.sync(incident_id)

    def calculate_risk(self, request: RiskRequest) -> RiskAssessment:
        denied = len(
            GovernanceQuery(self.audit_store, self.trace_store).query(
                actor_id=request.identity.user_id,
                decision=GovernanceDecision.DENIED,
            )
        )
        repair_failures = len(
            self.event_center.query(
                incident_id=request.incident_id,
                event_type=SecurityEventType.REPAIR_FAILED,
            )
        )
        assessment = self.risk_scorer.assess(
            request,
            authorization_denied_count=denied,
            repair_failed_count=repair_failures,
            assessment_id=self.id_factory(),
            now=self.clock(),
        )
        self.audit_store.record(
            incident_id=request.incident_id,
            event_type=AuditEventType.DECISION,
            actor="GovernanceService",
            action="risk_assessment_created",
            outcome="created",
            details={
                "assessment_id": assessment.assessment_id,
                "actor_id": assessment.actor_id,
                "actor_role": request.identity.roles[0].value,
                "action": request.action,
                "operation": request.operation.value,
                "risk_score": assessment.risk_score,
                "risk_level": assessment.risk_level.value,
                "decision": "assessed",
                "severity": assessment.risk_level.value,
                "reason_count": len(assessment.reasons),
            },
        )
        return assessment

    def generate_report(
        self,
        incident_id: str,
        report_type: ComplianceReportType,
        *,
        actor_id: str = "system",
    ) -> ComplianceReport:
        report = ComplianceReportService(
            self.audit_store,
            self.trace_store,
            clock=self.clock,
            id_factory=self.id_factory,
        ).build(incident_id, report_type)
        encoded = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        self.audit_store.record(
            incident_id=incident_id,
            event_type=AuditEventType.DECISION,
            actor="GovernanceService",
            action="compliance_report_generated",
            outcome="created",
            details={
                "report_id": report.report_id,
                "report_type": report.report_type.value,
                "actor_id": actor_id,
                "timeline_event_count": len(report.timeline),
                "security_event_count": len(report.security_events),
                "risk_level": (
                    report.risk_summary.latest_level.value
                    if report.risk_summary.latest_level is not None
                    else None
                ),
                "report_sha256": sha256(encoded).hexdigest(),
            },
        )
        return report

    def metrics(self) -> GovernanceMetricsSnapshot:
        security_events = self.event_center.query()
        authorization = GovernanceQuery(self.audit_store, self.trace_store).metrics()
        audit_events = self.audit_store.list_all_events(event_type=AuditEventType.DECISION)
        high_risk = sum(
            event.action == "risk_assessment_created"
            and event.details.get("risk_level") in {
                GovernanceRiskLevel.HIGH.value,
                GovernanceRiskLevel.CRITICAL.value,
            }
            for event in audit_events
        )
        reports = sum(
            event.action == "compliance_report_generated" for event in audit_events
        )
        return GovernanceMetricsSnapshot(
            security_events_total=len(security_events),
            authorization_denied_total=authorization.authorization_denied,
            high_risk_operations_total=high_risk,
            compliance_reports_total=reports,
        )


__all__ = ["GovernanceService"]
