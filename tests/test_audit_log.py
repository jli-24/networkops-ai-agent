"""Tests for the append-only enterprise audit log."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.audit import AuditEventType, SQLiteAuditLog


class AuditLogTests(unittest.TestCase):
    def test_persists_filters_and_redacts_events(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.sqlite3"
            audit = SQLiteAuditLog(path)
            first = audit.record(
                incident_id="INC-1001",
                event_type="agent_call",
                actor="DiagnosisAgent",
                action="diagnose",
                outcome="succeeded",
                details={
                    "api_key": "secret",
                    "nested": {"password": "hidden", "evidence": "LOG-1"},
                },
            )
            audit.record(
                incident_id="INC-1001",
                event_type="decision",
                actor="RiskCheck",
                action="classify",
                outcome="approval_required",
                details={"risk_level": "high"},
            )
            audit.record(
                incident_id="INC-2002",
                event_type="tool_call",
                actor="TopologyAgent",
                action="query_path",
                outcome="succeeded",
            )

            reopened = SQLiteAuditLog(path)
            events = reopened.list_events("INC-1001")
            decisions = reopened.list_events(
                "INC-1001", event_type=AuditEventType.DECISION
            )

            self.assertEqual([event.event_type for event in events], [
                AuditEventType.AGENT_CALL,
                AuditEventType.DECISION,
            ])
            self.assertEqual(decisions[0].action, "classify")
            self.assertEqual(first.details["api_key"], "***REDACTED***")
            self.assertEqual(
                first.details["nested"]["password"], "***REDACTED***"
            )
            self.assertEqual(first.details["nested"]["evidence"], "LOG-1")
            self.assertIsNotNone(first.created_at.tzinfo)

    def test_idempotency_key_prevents_duplicate_approval_event(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            arguments = {
                "incident_id": "INC-1001",
                "event_type": "approval",
                "actor": "operator",
                "action": "request",
                "outcome": "pending",
                "idempotency_key": "approval-request:digest",
            }

            first = audit.record(**arguments)
            second = audit.record(**arguments)

            self.assertEqual(first.event_id, second.event_id)
            self.assertEqual(len(audit.list_events("INC-1001")), 1)

    def test_rejects_unknown_event_type(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            with self.assertRaises(ValueError):
                audit.record(
                    incident_id="INC-1001",
                    event_type="unknown",
                    actor="system",
                    action="test",
                    outcome="failed",
                )


if __name__ == "__main__":
    unittest.main()
