"""PostgreSQL Audit and Trace store adapter tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import MagicMock

from network_agent_rag.audit import AuditEventType
from network_agent_rag.observability import SpanKind, SpanStatus
from network_agent_rag.storage.postgres import (
    PostgreSQLAuditStore,
    PostgreSQLTraceStore,
    open_postgres_stores,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


class _Context:
    def __init__(self, value) -> None:
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _Cursor:
    def __init__(self, rows: list[object] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql: str, parameters=None):
        self.calls.append((" ".join(sql.split()), parameters))
        return self

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = list(self.rows)
        self.rows.clear()
        return rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_value = cursor

    def cursor(self, **kwargs):
        return _Context(self.cursor_value)


class _Pool:
    def __init__(self, cursor: _Cursor) -> None:
        self.connection_value = _Connection(cursor)

    def connection(self):
        return _Context(self.connection_value)


def _span_row(**overrides):
    row = {
        "span_id": "span-1",
        "trace_id": "trace-1",
        "run_id": "run-1",
        "incident_id": "INC-1",
        "parent_span_id": None,
        "kind": "workflow",
        "name": "EnterpriseWorkflow",
        "status": "running",
        "started_at": NOW,
        "ended_at": None,
        "duration_ms": None,
        "attempt": 1,
        "error_code": None,
        "input_summary_hash": None,
        "output_summary_hash": None,
        "attributes": {},
        "idempotency_key": None,
    }
    row.update(overrides)
    return row


def _audit_row(**overrides):
    row = {
        "event_id": "event-1",
        "incident_id": "INC-1",
        "event_type": "decision",
        "actor": "supervisor",
        "action": "route",
        "outcome": "selected",
        "details": {},
        "created_at": NOW,
        "idempotency_key": "route:1",
    }
    row.update(overrides)
    return row


class PostgreSQLStoreMockTests(unittest.TestCase):
    def test_setup_creates_separate_audit_and_trace_tables(self) -> None:
        cursor = _Cursor()
        pool = _Pool(cursor)

        PostgreSQLAuditStore(pool).setup()
        PostgreSQLTraceStore(pool).setup()

        sql = " ".join(item[0] for item in cursor.calls)
        self.assertIn("networkops_audit_events", sql)
        self.assertIn("networkops_trace_spans", sql)
        self.assertNotIn("checkpoint", sql.lower())
        self.assertTrue(all(parameters is None for _, parameters in cursor.calls))

    def test_audit_record_uses_parameters_and_redacts_jsonb(self) -> None:
        cursor = _Cursor()
        store = PostgreSQLAuditStore(_Pool(cursor))

        event = store.record(
            incident_id="INC-1",
            event_type=AuditEventType.TOOL_CALL,
            actor="agent",
            action="query",
            outcome="succeeded",
            details={"password": "secret", "result_count": 2},
        )

        sql, parameters = cursor.calls[-1]
        self.assertIn("INSERT INTO networkops_audit_events", sql)
        self.assertNotIn("INC-1", sql)
        self.assertIn("INC-1", parameters)
        self.assertEqual(event.details["password"], "***REDACTED***")
        self.assertEqual(event.details["result_count"], 2)

    def test_audit_idempotency_uses_database_conflict_resolution(self) -> None:
        cursor = _Cursor([None, _audit_row()])
        store = PostgreSQLAuditStore(_Pool(cursor))

        event = store.record(
            incident_id="INC-1",
            event_type=AuditEventType.DECISION,
            actor="supervisor",
            action="route",
            outcome="selected",
            idempotency_key="route:1",
        )

        self.assertEqual(event.event_id, "event-1")
        self.assertTrue(any("ON CONFLICT" in sql for sql, _ in cursor.calls))

    def test_trace_start_and_finish_use_attempt_query_and_row_lock(self) -> None:
        start_cursor = _Cursor([{"attempt_count": 0}])
        store = PostgreSQLTraceStore(_Pool(start_cursor))
        started = store.start_span(
            trace_id="trace-1",
            run_id="run-1",
            incident_id="INC-1",
            kind=SpanKind.WORKFLOW,
            name="EnterpriseWorkflow",
            started_at=NOW,
        )
        self.assertEqual(started.attempt, 1)
        lock_calls = [
            parameters
            for sql, parameters in start_cursor.calls
            if "pg_advisory_xact_lock" in sql
        ]
        self.assertEqual(len(lock_calls), 1)
        self.assertNotIn("\0", lock_calls[0][0])
        self.assertTrue(any("COUNT(*)" in sql for sql, _ in start_cursor.calls))
        self.assertTrue(any("INSERT INTO networkops_trace_spans" in sql for sql, _ in start_cursor.calls))

        finish_cursor = _Cursor([_span_row(span_id=started.span_id)])
        store.pool = _Pool(finish_cursor)
        finished = store.finish_span(
            started.span_id,
            status=SpanStatus.SUCCEEDED,
            ended_at=NOW + timedelta(seconds=2),
        )
        self.assertEqual(finished.duration_ms, 2000.0)
        self.assertTrue(any("FOR UPDATE" in sql for sql, _ in finish_cursor.calls))
        self.assertTrue(any("UPDATE networkops_trace_spans" in sql for sql, _ in finish_cursor.calls))

    def test_postgres_lifecycle_closes_owned_pool(self) -> None:
        pool = MagicMock()
        pool.connection.return_value = _Context(_Connection(_Cursor()))
        pool_factory = MagicMock(return_value=pool)

        with open_postgres_stores("postgresql://example", pool_factory=pool_factory) as stores:
            self.assertIsInstance(stores[0], PostgreSQLAuditStore)
            self.assertIsInstance(stores[1], PostgreSQLTraceStore)

        pool_factory.assert_called_once()
        pool.close.assert_called_once_with()


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PostgreSQLStoreIntegrationTests(unittest.TestCase):
    def test_round_trip_contract(self) -> None:
        database_url = os.environ["TEST_DATABASE_URL"]
        with open_postgres_stores(database_url) as (audit, trace):
            incident_id = f"integration-{datetime.now(timezone.utc).timestamp()}"
            event = audit.record(
                incident_id=incident_id,
                event_type=AuditEventType.DECISION,
                actor="test",
                action="route",
                outcome="selected",
            )
            span = trace.start_span(
                trace_id=incident_id,
                run_id="run-1",
                incident_id=incident_id,
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
            )
            finished = trace.finish_span(span.span_id, status=SpanStatus.SUCCEEDED)

            self.assertEqual(audit.list_events(incident_id), [event])
            self.assertEqual(trace.list_spans(incident_id), [finished])


if __name__ == "__main__":
    unittest.main()
