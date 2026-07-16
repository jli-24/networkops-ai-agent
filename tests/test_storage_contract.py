"""Storage protocol compatibility tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.storage.base import AuditStore, TraceStore
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.observability import SQLiteTraceStore, SpanKind, SpanStatus


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


class StorageContractTests(unittest.TestCase):
    def test_existing_sqlite_stores_satisfy_runtime_protocols(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace = SQLiteTraceStore(Path(directory) / "trace.sqlite3")

            self.assertIsInstance(audit, AuditStore)
            self.assertIsInstance(trace, TraceStore)

    def test_sqlite_audit_contract_preserves_order_and_idempotency(self) -> None:
        with TemporaryDirectory() as directory:
            store: AuditStore = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            first = store.record(
                incident_id="INC-1",
                event_type=AuditEventType.DECISION,
                actor="supervisor",
                action="route",
                outcome="selected",
                details={"token": "secret", "next": "diagnosis"},
                idempotency_key="route:1",
            )
            duplicate = store.record(
                incident_id="INC-1",
                event_type=AuditEventType.DECISION,
                actor="supervisor",
                action="route",
                outcome="selected",
                idempotency_key="route:1",
            )
            store.record(
                incident_id="INC-1",
                event_type=AuditEventType.APPROVAL,
                actor="operator",
                action="approve",
                outcome="approved",
            )

            self.assertEqual(first.event_id, duplicate.event_id)
            events = store.list_events("INC-1")
            self.assertEqual([item.event_type for item in events], [
                AuditEventType.DECISION,
                AuditEventType.APPROVAL,
            ])
            self.assertEqual(events[0].details["token"], "***REDACTED***")
            self.assertEqual(len(store.list_all_events()), 2)

    def test_sqlite_trace_contract_preserves_lifecycle_and_pagination(self) -> None:
        with TemporaryDirectory() as directory:
            store: TraceStore = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            started = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
                started_at=NOW,
                idempotency_key="workflow:1",
            )
            duplicate = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
                started_at=NOW,
                idempotency_key="workflow:1",
            )
            finished = store.finish_span(
                started.span_id,
                status=SpanStatus.SUCCEEDED,
                ended_at=NOW + timedelta(seconds=1),
            )

            self.assertEqual(started.span_id, duplicate.span_id)
            self.assertEqual(finished.duration_ms, 1000.0)
            self.assertEqual(store.trace_id_for_incident("INC-1"), "trace-1")
            self.assertEqual(store.list_spans("INC-1"), [finished])
            incidents, cursor = store.list_incidents(limit=1)
            self.assertEqual([item.incident_id for item in incidents], ["INC-1"])
            self.assertIsNone(cursor)


if __name__ == "__main__":
    unittest.main()
