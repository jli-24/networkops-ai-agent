"""Read-only compliance reports and minimal generation audit facts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.governance import (
    ComplianceReportService,
    ComplianceReportType,
    GovernanceService,
)
from network_agent_rag.observability import SQLiteTraceStore


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


class ComplianceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.audit = SQLiteAuditLog(root / "audit.sqlite3")
        self.trace = SQLiteTraceStore(root / "trace.sqlite3")
        self.audit.record(
            incident_id="INC-REPORT-1",
            event_type=AuditEventType.DECISION,
            actor="RiskCheck",
            action="evaluate_risk",
            outcome="approval_required",
            details={"risk_level": "high"},
        )
        self.audit.record(
            incident_id="INC-REPORT-1",
            event_type=AuditEventType.APPROVAL,
            actor="admin-1",
            action="approve",
            outcome="approved",
            details={"actor_id": "admin-1"},
        )
        self.audit.record(
            incident_id="INC-REPORT-1",
            event_type=AuditEventType.TOOL_CALL,
            actor="Execute",
            action="ACTION-1",
            outcome="succeeded",
            details={"target": "SW1"},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builder_is_read_only_and_marks_missing_verification(self) -> None:
        builder = ComplianceReportService(
            self.audit,
            self.trace,
            clock=lambda: NOW,
            id_factory=lambda: "report-1",
        )
        before = self.audit.list_all_events()

        report = builder.build(
            "INC-REPORT-1",
            ComplianceReportType.INCIDENT_HANDLING,
        )

        self.assertEqual(report.report_id, "report-1")
        self.assertEqual(report.actors, ("RiskCheck", "admin-1", "Execute"))
        self.assertEqual(len(report.approvals), 1)
        self.assertEqual(len(report.execution), 1)
        self.assertEqual(report.verification, ())
        self.assertIn("verification", report.missing_evidence)
        self.assertEqual(self.audit.list_all_events(), before)

    def test_all_report_types_use_the_same_read_only_evidence_contract(self) -> None:
        builder = ComplianceReportService(
            self.audit,
            self.trace,
            clock=lambda: NOW,
            id_factory=lambda: "report-1",
        )
        before = self.audit.list_all_events()

        for report_type in ComplianceReportType:
            with self.subTest(report_type=report_type):
                report = builder.build("INC-REPORT-1", report_type)
                self.assertEqual(report.report_type, report_type)
                self.assertTrue(report.timeline)
                self.assertTrue(report.actions)

        self.assertEqual(self.audit.list_all_events(), before)

    def test_governance_service_records_only_report_reference_and_hash(self) -> None:
        service = GovernanceService(
            self.audit,
            self.trace,
            clock=lambda: NOW,
            id_factory=lambda: "report-1",
        )

        report = service.generate_report(
            "INC-REPORT-1",
            ComplianceReportType.SECURITY_EVENT,
            actor_id="auditor-1",
        )
        generated = [
            event
            for event in self.audit.list_events("INC-REPORT-1")
            if event.action == "compliance_report_generated"
        ]

        self.assertEqual(report.report_type, ComplianceReportType.SECURITY_EVENT)
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].details["report_id"], "report-1")
        self.assertEqual(len(str(generated[0].details["report_sha256"])), 64)
        self.assertNotIn("timeline", generated[0].details)
        self.assertNotIn("security_events", generated[0].details)

    def test_version_is_0_11_0(self) -> None:
        import network_agent_rag

        self.assertEqual(network_agent_rag.__version__, "0.15.0")


if __name__ == "__main__":
    unittest.main()
