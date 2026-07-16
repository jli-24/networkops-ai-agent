"""Enterprise metrics aggregation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from network_agent_rag.api.enterprise import create_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.observability import (
    CallSummary,
    LatencySummary,
    MetricsSnapshot,
    RagRetrievalSummary,
    RepairSummary,
    SQLiteMetricsStore,
    SQLiteTraceStore,
    SpanKind,
    SpanStatus,
    TraceEvent,
)


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def finish_span(
    store: SQLiteTraceStore,
    *,
    incident_id: str,
    run_id: str,
    kind: SpanKind,
    name: str,
    duration_ms: float,
    status: SpanStatus = SpanStatus.SUCCEEDED,
    attributes: dict[str, object] | None = None,
) -> None:
    started_at = NOW + timedelta(seconds=len(store.list_all_spans()))
    span = store.start_span(
        trace_id=f"trace-{incident_id}",
        run_id=run_id,
        incident_id=incident_id,
        kind=kind,
        name=name,
        started_at=started_at,
    )
    store.finish_span(
        span.span_id,
        status=status,
        ended_at=started_at + timedelta(milliseconds=duration_ms),
        attributes=attributes,
    )


def record_trace_event(
    audit: SQLiteAuditLog,
    *,
    incident_id: str,
    trace_id: str,
    run_id: str,
    event_type: str,
    timestamp: datetime,
) -> None:
    event = TraceEvent(
        timestamp=timestamp,
        incident_id=incident_id,
        trace_id=trace_id,
        run_id=run_id,
        agent_name="Approval",
        node_name="Approval",
        event_type=event_type,
        input=None,
        output=None,
        latency=None,
        status="interrupted" if event_type == "approval_pause" else "resumed",
    )
    audit.record(
        incident_id=incident_id,
        event_type=AuditEventType.TRACE,
        actor="Approval",
        action=event_type,
        outcome=event.status.value,
        details={"trace_event": event.model_dump(mode="json")},
        idempotency_key=f"{trace_id}:{run_id}:{event_type}",
    )


class MetricsModelTests(unittest.TestCase):
    def test_models_are_strict_and_reject_invalid_values(self) -> None:
        with self.assertRaises(ValidationError):
            LatencySummary(
                count="1",
                average_ms=0,
                p50_ms=0,
                p95_ms=0,
                max_ms=0,
            )
        with self.assertRaises(ValidationError):
            LatencySummary(
                count=1,
                average_ms=float("nan"),
                p50_ms=0,
                p95_ms=0,
                max_ms=0,
            )
        with self.assertRaises(ValidationError):
            LatencySummary(
                count=-1,
                average_ms=0,
                p50_ms=0,
                p95_ms=0,
                max_ms=0,
            )
        with self.assertRaises(ValidationError):
            CallSummary(
                total=1,
                succeeded=1,
                failed=0,
                interrupted=0,
                failure_rate_percent=101,
            )
        with self.assertRaises(ValidationError):
            RagRetrievalSummary(
                total=0,
                succeeded=0,
                failed=0,
                interrupted=0,
                unexpected=True,
            )
        with self.assertRaises(ValidationError):
            RepairSummary(
                total=0,
                succeeded=0,
                failed=0,
                blocked=0,
                not_executed=0,
                success_rate_percent=-1,
            )
        frozen = LatencySummary(
            count=0,
            average_ms=0,
            p50_ms=0,
            p95_ms=0,
            max_ms=0,
        )
        with self.assertRaises(ValidationError):
            frozen.count = 1

    def test_models_reject_inconsistent_summaries(self) -> None:
        with self.assertRaises(ValidationError):
            LatencySummary(
                count=0,
                average_ms=1,
                p50_ms=0,
                p95_ms=0,
                max_ms=0,
            )
        with self.assertRaises(ValidationError):
            LatencySummary(
                count=1,
                average_ms=1,
                p50_ms=2,
                p95_ms=1,
                max_ms=2,
            )
        with self.assertRaises(ValidationError):
            CallSummary(
                total=2,
                succeeded=1,
                failed=0,
                interrupted=0,
                failure_rate_percent=0,
            )
        with self.assertRaises(ValidationError):
            CallSummary(
                total=1,
                succeeded=0,
                failed=1,
                interrupted=0,
                failure_rate_percent=0,
            )
        with self.assertRaises(ValidationError):
            RagRetrievalSummary(
                total=1,
                succeeded=0,
                failed=0,
                interrupted=0,
            )
        with self.assertRaises(ValidationError):
            RepairSummary(
                total=1,
                succeeded=1,
                failed=0,
                blocked=0,
                not_executed=0,
                success_rate_percent=0,
            )


class SQLiteMetricsStoreTests(unittest.TestCase):
    def test_empty_store_returns_stable_zero_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")

            snapshot = SQLiteMetricsStore(trace_store, audit).snapshot()

            self.assertIsInstance(snapshot, MetricsSnapshot)
            self.assertEqual(
                snapshot.model_dump(mode="json"),
                {
                    "workflow_latency": {
                        "count": 0,
                        "average_ms": 0.0,
                        "p50_ms": 0.0,
                        "p95_ms": 0.0,
                        "max_ms": 0.0,
                    },
                    "agent_latency": {
                        "count": 0,
                        "average_ms": 0.0,
                        "p50_ms": 0.0,
                        "p95_ms": 0.0,
                        "max_ms": 0.0,
                    },
                    "tool_calls": {
                        "total": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "interrupted": 0,
                        "failure_rate_percent": 0.0,
                    },
                    "rag_retrievals": {
                        "total": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "interrupted": 0,
                    },
                    "approval_waiting_time": {
                        "count": 0,
                        "average_ms": 0.0,
                        "p50_ms": 0.0,
                        "p95_ms": 0.0,
                        "max_ms": 0.0,
                    },
                    "repair_executions": {
                        "total": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "blocked": 0,
                        "not_executed": 0,
                        "success_rate_percent": 0.0,
                    },
                },
            )

    def test_aggregates_latency_calls_rag_and_repairs(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            for index, duration in enumerate((100.0, 300.0), 1):
                finish_span(
                    trace_store,
                    incident_id=f"INC-W{index}",
                    run_id=f"run-w{index}",
                    kind=SpanKind.WORKFLOW,
                    name="EnterpriseWorkflow",
                    duration_ms=duration,
                )
            for index, duration in enumerate((10.0, 30.0), 1):
                finish_span(
                    trace_store,
                    incident_id=f"INC-A{index}",
                    run_id=f"run-a{index}",
                    kind=SpanKind.AGENT,
                    name="DiagnosisAgent",
                    duration_ms=duration,
                )
            finish_span(
                trace_store,
                incident_id="INC-T1",
                run_id="run-t1",
                kind=SpanKind.TOOL,
                name="search_knowledge",
                duration_ms=5,
            )
            finish_span(
                trace_store,
                incident_id="INC-T2",
                run_id="run-t2",
                kind=SpanKind.TOOL,
                name="search_knowledge",
                duration_ms=10,
                status=SpanStatus.FAILED,
            )
            finish_span(
                trace_store,
                incident_id="INC-T3",
                run_id="run-t3",
                kind=SpanKind.TOOL,
                name="query_metrics",
                duration_ms=15,
            )
            finish_span(
                trace_store,
                incident_id="INC-T4",
                run_id="run-t4",
                kind=SpanKind.TOOL,
                name="query_logs",
                duration_ms=20,
                status=SpanStatus.INTERRUPTED,
            )
            trace_store.start_span(
                trace_id="trace-running",
                run_id="run-running",
                incident_id="INC-RUNNING",
                kind=SpanKind.TOOL,
                name="query_topology",
                started_at=NOW,
            )
            for index, execution_status in enumerate(
                ("succeeded", "failed", "blocked", "not_executed"), 1
            ):
                finish_span(
                    trace_store,
                    incident_id=f"INC-E{index}",
                    run_id=f"run-e{index}",
                    kind=SpanKind.EXECUTION,
                    name="Execute",
                    duration_ms=25,
                    attributes={"execution_status": execution_status},
                )

            snapshot = SQLiteMetricsStore(trace_store, audit).snapshot()

            self.assertEqual(snapshot.workflow_latency.average_ms, 200.0)
            self.assertEqual(snapshot.workflow_latency.p50_ms, 200.0)
            self.assertEqual(snapshot.workflow_latency.p95_ms, 290.0)
            self.assertEqual(snapshot.workflow_latency.max_ms, 300.0)
            self.assertEqual(snapshot.agent_latency.average_ms, 20.0)
            self.assertEqual(snapshot.agent_latency.p95_ms, 29.0)
            self.assertEqual(snapshot.tool_calls.total, 4)
            self.assertEqual(snapshot.tool_calls.succeeded, 2)
            self.assertEqual(snapshot.tool_calls.failed, 1)
            self.assertEqual(snapshot.tool_calls.interrupted, 1)
            self.assertEqual(snapshot.tool_calls.failure_rate_percent, 25.0)
            self.assertEqual(snapshot.rag_retrievals.total, 2)
            self.assertEqual(snapshot.rag_retrievals.succeeded, 1)
            self.assertEqual(snapshot.rag_retrievals.failed, 1)
            self.assertEqual(snapshot.repair_executions.total, 4)
            self.assertEqual(snapshot.repair_executions.succeeded, 1)
            self.assertEqual(snapshot.repair_executions.failed, 1)
            self.assertEqual(snapshot.repair_executions.blocked, 1)
            self.assertEqual(snapshot.repair_executions.not_executed, 1)
            self.assertEqual(snapshot.repair_executions.success_rate_percent, 25.0)

    def test_pairs_completed_approval_waits_fifo_and_survives_reopen(self) -> None:
        with TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.sqlite3"
            audit_path = Path(directory) / "audit.sqlite3"
            trace_store = SQLiteTraceStore(trace_path)
            audit = SQLiteAuditLog(audit_path)
            events = (
                ("trace-a", "run-1", "approval_pause", 0),
                ("trace-a", "run-2", "resume", 1),
                ("trace-a", "run-3", "resume", 1.5),
                ("trace-a", "run-4", "approval_pause", 2),
                ("trace-a", "run-5", "resume", 4),
                ("trace-a", "run-6", "approval_pause", 5),
                ("trace-b", "run-7", "approval_pause", 10),
                ("trace-b", "run-8", "resume", 13),
            )
            for trace_id, run_id, event_type, seconds in events:
                record_trace_event(
                    audit,
                    incident_id="INC-APPROVAL",
                    trace_id=trace_id,
                    run_id=run_id,
                    event_type=event_type,
                    timestamp=NOW + timedelta(seconds=seconds),
                )

            first = SQLiteMetricsStore(trace_store, audit).snapshot()
            reopened = SQLiteMetricsStore(
                SQLiteTraceStore(trace_path), SQLiteAuditLog(audit_path)
            ).snapshot()

            self.assertEqual(first, reopened)
            self.assertEqual(first.approval_waiting_time.count, 3)
            self.assertEqual(first.approval_waiting_time.average_ms, 2000.0)
            self.assertEqual(first.approval_waiting_time.p50_ms, 2000.0)
            self.assertEqual(first.approval_waiting_time.p95_ms, 2900.0)
            self.assertEqual(first.approval_waiting_time.max_ms, 3000.0)

    def test_skips_malformed_trace_rows_without_losing_valid_waits(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            audit.record(
                incident_id="INC-BAD",
                event_type=AuditEventType.TRACE,
                actor="legacy",
                action="legacy_trace",
                outcome="unknown",
                details={"trace_event": {"legacy": True}},
            )
            record_trace_event(
                audit,
                incident_id="INC-GOOD",
                trace_id="trace-good",
                run_id="run-pause",
                event_type="approval_pause",
                timestamp=NOW,
            )
            record_trace_event(
                audit,
                incident_id="INC-GOOD",
                trace_id="trace-good",
                run_id="run-resume",
                event_type="resume",
                timestamp=NOW + timedelta(seconds=2),
            )

            snapshot = SQLiteMetricsStore(trace_store, audit).snapshot()

            self.assertEqual(snapshot.approval_waiting_time.count, 1)
            self.assertEqual(snapshot.approval_waiting_time.average_ms, 2000.0)


class EnterpriseMetricsApiTests(unittest.TestCase):
    def test_uses_explicit_metrics_store(self) -> None:
        class FixedMetricsStore:
            calls = 0

            def snapshot(self) -> MetricsSnapshot:
                self.calls += 1
                zero_latency = LatencySummary(
                    count=0,
                    average_ms=0,
                    p50_ms=0,
                    p95_ms=0,
                    max_ms=0,
                )
                return MetricsSnapshot(
                    workflow_latency=zero_latency,
                    agent_latency=zero_latency,
                    tool_calls=CallSummary(
                        total=1,
                        succeeded=1,
                        failed=0,
                        interrupted=0,
                        failure_rate_percent=0,
                    ),
                    rag_retrievals=RagRetrievalSummary(
                        total=0,
                        succeeded=0,
                        failed=0,
                        interrupted=0,
                    ),
                    approval_waiting_time=zero_latency,
                    repair_executions=RepairSummary(
                        total=0,
                        succeeded=0,
                        failed=0,
                        blocked=0,
                        not_executed=0,
                        success_rate_percent=0,
                    ),
                )

        store = FixedMetricsStore()
        app = create_enterprise_app(metrics_store=store)

        with TestClient(app) as client:
            response = client.get("/api/v1/enterprise/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_calls"]["total"], 1)
        self.assertEqual(store.calls, 1)

    def test_builds_sqlite_store_and_preserves_existing_metrics_routes(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            finish_span(
                trace_store,
                incident_id="INC-API",
                run_id="run-api",
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
                duration_ms=125,
            )
            app = create_enterprise_app(trace_store=trace_store, audit_log=audit)

            with TestClient(app) as client:
                enterprise = client.get("/api/v1/enterprise/metrics")
                legacy = client.get("/api/v1/observability/metrics/summary")
                prometheus = client.get("/metrics")

            self.assertEqual(enterprise.status_code, 200)
            self.assertEqual(enterprise.json()["workflow_latency"]["count"], 1)
            self.assertEqual(legacy.status_code, 200)
            self.assertEqual(legacy.json()["incidents_total"], 1)
            self.assertEqual(prometheus.status_code, 200)

    def test_returns_503_without_metrics_store_or_trace_store(self) -> None:
        app = create_enterprise_app()

        with TestClient(app) as client:
            response = client.get("/api/v1/enterprise/metrics")

        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
