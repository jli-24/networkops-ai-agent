"""Security-event projections and Governance Service read/write boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.governance import (
    GovernanceService,
    GovernanceSeverity,
    SecurityEventType,
)
from network_agent_rag.observability import SpanKind, SpanStatus, SQLiteTraceStore
from network_agent_rag.observability.deployment import (
    RequestMetrics,
    render_deployment_metrics,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


class GovernanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audit = SQLiteAuditLog(root / "audit.sqlite3")
        self.trace = SQLiteTraceStore(root / "trace.sqlite3")
        self.identifiers = iter(f"id-{index}" for index in range(100))
        self.service = GovernanceService(
            self.audit,
            self.trace,
            clock=lambda: NOW,
            id_factory=lambda: next(self.identifiers),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _audit(
        self,
        action: str,
        outcome: str,
        *,
        event_type: AuditEventType = AuditEventType.DECISION,
        details: dict[str, object] | None = None,
        incident_id: str = "INC-GOV-1",
        actor: str = "system",
    ) -> None:
        self.audit.record(
            incident_id=incident_id,
            event_type=event_type,
            actor=actor,
            action=action,
            outcome=outcome,
            details=details,
        )

    def test_projects_all_security_event_types_without_copying_payloads(self) -> None:
        self._audit(
            "authenticate_failed",
            "denied",
            details={"actor_id": "anonymous", "token": "never-store"},
        )
        self._audit(
            "authorize_execution",
            "denied",
            details={"actor_id": "engineer-1", "permission": "EXECUTE_REPAIR"},
        )
        self._audit("policy_violation", "denied", details={"actor_id": "admin-1"})
        self._audit(
            "evaluate_risk",
            "approval_required",
            details={"risk_level": "high"},
        )
        self._audit(
            "risk_assessment_created",
            "created",
            details={"actor_id": "engineer-1", "risk_level": "critical"},
        )
        self._audit(
            "ACTION-1",
            "failed",
            event_type=AuditEventType.TOOL_CALL,
            details={"message": "device failed", "password": "never-store"},
            actor="Execute",
        )
        self._audit(
            "session_revoked",
            "allowed",
            details={"actor_id": "engineer-1", "session_id": "session-1"},
        )

        events = self.service.query_events(incident_id="INC-GOV-1")

        self.assertEqual(
            {event.event_type for event in events},
            set(SecurityEventType),
        )
        critical = next(
            event
            for event in events
            if event.event_type == SecurityEventType.HIGH_RISK_OPERATION
            and event.severity == GovernanceSeverity.CRITICAL
        )
        self.assertEqual(critical.actor_id, "engineer-1")
        serialized = str([event.model_dump(mode="json") for event in events])
        self.assertNotIn("never-store", serialized)
        self.assertNotIn("device failed", serialized)

    def test_audit_repair_failure_prevents_duplicate_trace_fallback(self) -> None:
        self._audit(
            "ACTION-1",
            "failed",
            event_type=AuditEventType.TOOL_CALL,
            actor="Execute",
        )
        span = self.trace.start_span(
            trace_id="trace-1",
            run_id="run-1",
            incident_id="INC-GOV-1",
            kind=SpanKind.EXECUTION,
            name="Execute",
            started_at=NOW,
        )
        self.trace.finish_span(
            span.span_id,
            status=SpanStatus.FAILED,
            ended_at=NOW + timedelta(seconds=1),
            error_code="FAILED",
        )

        failures = self.service.query_events(
            incident_id="INC-GOV-1",
            event_type=SecurityEventType.REPAIR_FAILED,
        )

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].source.value, "audit")

    def test_trace_failure_is_used_when_audit_fact_is_absent(self) -> None:
        span = self.trace.start_span(
            trace_id="trace-1",
            run_id="run-1",
            incident_id="INC-GOV-1",
            kind=SpanKind.EXECUTION,
            name="Execute",
            started_at=NOW,
        )
        self.trace.finish_span(
            span.span_id,
            status=SpanStatus.FAILED,
            ended_at=NOW + timedelta(seconds=1),
            error_code="FAILED",
        )

        failures = self.service.query_events(
            incident_id="INC-GOV-1",
            event_type=SecurityEventType.REPAIR_FAILED,
        )

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].source.value, "trace")

    def test_query_and_metrics_are_read_only_but_sync_is_idempotent(self) -> None:
        self._audit(
            "authorize_execution",
            "denied",
            details={"actor_id": "engineer-1", "permission": "EXECUTE_REPAIR"},
        )
        before = self.audit.list_all_events()

        queried = self.service.query_events(actor_id="engineer-1")
        metrics = self.service.metrics()

        self.assertEqual(len(queried), 1)
        self.assertEqual(metrics.authorization_denied_total, 1)
        self.assertEqual(self.audit.list_all_events(), before)

        first = self.service.sync_security_events(incident_id="INC-GOV-1")
        second = self.service.sync_security_events(incident_id="INC-GOV-1")
        generated = [
            event
            for event in self.audit.list_events("INC-GOV-1")
            if event.action == "security_event_created"
        ]

        self.assertEqual(first, second)
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].details["source_event_id"], first[0].source_event_id)
        self.assertNotIn("details", generated[0].details)

    def test_unknown_authorization_action_is_not_projected(self) -> None:
        self._audit(
            "authorize_unrecognized_operation",
            "denied",
            details={"actor_id": "engineer-1", "permission": "EXECUTE_REPAIR"},
        )

        self.assertEqual(self.service.query_events(incident_id="INC-GOV-1"), ())

    def test_prometheus_exposes_low_cardinality_governance_metrics(self) -> None:
        self._audit(
            "authorize_execution",
            "denied",
            details={
                "actor_id": "engineer-1",
                "actor_role": "Engineer",
                "permission": "EXECUTE_REPAIR",
            },
        )
        self._audit(
            "risk_assessment_created",
            "created",
            details={"risk_level": "high"},
        )
        self._audit(
            "compliance_report_generated",
            "created",
            details={"report_type": "incident_handling"},
        )

        rendered = render_deployment_metrics(
            self.trace,
            self.audit,
            RequestMetrics(),
        )

        self.assertIn(
            'networkops_security_events_total{event_type="authorization_denied",severity="medium"} 1',
            rendered,
        )
        self.assertIn(
            'networkops_authorization_denied_total{permission="EXECUTE_REPAIR"} 1',
            rendered,
        )
        self.assertIn(
            'networkops_high_risk_operations_total{risk_level="high"} 1',
            rendered,
        )
        self.assertIn(
            'networkops_compliance_reports_total{report_type="incident_handling"} 1',
            rendered,
        )
        self.assertNotIn("engineer-1", rendered)
        self.assertNotIn("INC-GOV-1", rendered)


if __name__ == "__main__":
    unittest.main()
