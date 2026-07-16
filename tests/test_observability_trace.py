"""Execution trace persistence tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
from threading import get_ident
import unittest

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from network_agent_rag.api.enterprise import _stream_workflow, create_enterprise_app
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.observability import SQLiteTraceStore, SpanKind, SpanStatus
from tests.test_approval_flow import _events
from tests.test_checkpoint import build_graph


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


class TraceStoreTests(unittest.TestCase):
    def test_persists_parent_child_spans_with_duration_and_redaction(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "traces.sqlite3"
            store = SQLiteTraceStore(path)
            workflow = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
                started_at=NOW,
                attributes={
                    "token": "secret",
                    "private_key": "private material",
                    "system_prompt": "full prompt",
                    "page_content": "full document",
                    "exception_stack": "full stack",
                    "risk_level": "high",
                    "document_count": 2,
                    "input_summary_hash": "c" * 64,
                },
                input_summary_hash="a" * 64,
            )
            agent = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                parent_span_id=workflow.span_id,
                kind=SpanKind.AGENT,
                name="DiagnosisAgent",
                started_at=NOW + timedelta(seconds=1),
            )
            store.finish_span(
                agent.span_id,
                status=SpanStatus.SUCCEEDED,
                ended_at=NOW + timedelta(seconds=3),
                attributes={"relevance_score": 0.9},
            )
            store.finish_span(
                workflow.span_id,
                status=SpanStatus.SUCCEEDED,
                ended_at=NOW + timedelta(seconds=4),
                output_summary_hash="b" * 64,
            )

            reopened = SQLiteTraceStore(path)
            spans = reopened.list_spans("INC-1")

            self.assertEqual([span.name for span in spans], ["EnterpriseWorkflow", "DiagnosisAgent"])
            self.assertEqual(spans[0].attributes["token"], "***REDACTED***")
            for key in (
                "private_key",
                "system_prompt",
                "page_content",
                "exception_stack",
            ):
                with self.subTest(key=key):
                    self.assertEqual(spans[0].attributes[key], "***REDACTED***")
            self.assertEqual(spans[0].attributes["document_count"], 2)
            self.assertEqual(spans[0].attributes["input_summary_hash"], "c" * 64)
            self.assertEqual(spans[0].duration_ms, 4000.0)
            self.assertEqual(spans[0].input_summary_hash, "a" * 64)
            self.assertEqual(spans[0].output_summary_hash, "b" * 64)
            self.assertEqual(spans[1].parent_span_id, spans[0].span_id)
            self.assertEqual(spans[1].duration_ms, 2000.0)
            self.assertEqual(spans[1].attributes["relevance_score"], 0.9)

    def test_tracks_attempts_and_idempotent_span_creation(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            first = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.TOOL,
                name="query_metrics",
                idempotency_key="tool:metrics:1",
            )
            duplicate = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.TOOL,
                name="query_metrics",
                idempotency_key="tool:metrics:1",
            )
            retry = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.TOOL,
                name="query_metrics",
            )

            self.assertEqual(first.span_id, duplicate.span_id)
            self.assertEqual(first.attempt, 1)
            self.assertEqual(retry.attempt, 2)

    def test_rejects_cross_trace_parent_and_naive_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            parent = store.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind=SpanKind.WORKFLOW,
                name="workflow",
            )
            with self.assertRaisesRegex(ValueError, "parent"):
                store.start_span(
                    trace_id="trace-2",
                    run_id="run-2",
                    incident_id="INC-2",
                    parent_span_id=parent.span_id,
                    kind=SpanKind.AGENT,
                    name="agent",
                )
            with self.assertRaisesRegex(ValueError, "timezone"):
                store.finish_span(
                    parent.span_id,
                    status=SpanStatus.SUCCEEDED,
                    ended_at=datetime(2026, 7, 16, 9, 0),
                )


class EnterpriseTraceTests(unittest.TestCase):
    def test_cancelled_stream_closes_workflow_span_as_interrupted(self) -> None:
        class CancelledWorkflow:
            async def astream(self, *_args: object, **_kwargs: object):
                raise asyncio.CancelledError
                yield  # pragma: no cover

        with TemporaryDirectory() as directory:
            finish_threads: list[int] = []

            class RecordingTraceStore(SQLiteTraceStore):
                def finish_span(self, *args, **kwargs):
                    finish_threads.append(get_ident())
                    return super().finish_span(*args, **kwargs)

            trace_store = RecordingTraceStore(Path(directory) / "traces.sqlite3")
            span = trace_store.start_span(
                trace_id="trace-cancelled",
                run_id="run-cancelled",
                incident_id="INC-CANCELLED",
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
            )

            async def consume() -> None:
                async for _ in _stream_workflow(
                    CancelledWorkflow(),
                    {},
                    {"configurable": {"thread_id": "INC-CANCELLED"}},
                    "INC-CANCELLED",
                    trace_store,
                    span.span_id,
                ):
                    pass

            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(consume())

            stored = trace_store.list_spans("INC-CANCELLED")
            self.assertEqual(stored[0].status, SpanStatus.INTERRUPTED)
            self.assertNotEqual(finish_threads, [get_ident()])

    def test_interrupt_and_resume_share_trace_with_distinct_runs(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(InMemorySaver(), audit, calls, trace_store)
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_store=trace_store,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-TRACE",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                resumed = client.post(
                    "/api/v1/incidents/INC-TRACE/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

            self.assertEqual(started.status_code, 200)
            self.assertEqual(resumed.status_code, 200)
            spans = trace_store.list_spans("INC-TRACE")
            workflows = [span for span in spans if span.kind == SpanKind.WORKFLOW]
            self.assertEqual(len(workflows), 2)
            self.assertEqual({span.trace_id for span in workflows}, {workflows[0].trace_id})
            self.assertEqual(len({span.run_id for span in workflows}), 2)
            self.assertEqual(
                [span.status for span in workflows],
                [SpanStatus.INTERRUPTED, SpanStatus.SUCCEEDED],
            )
            self.assertIn("DiagnosisAgent", {span.name for span in spans})
            self.assertIn("query_metrics", {span.name for span in spans})
            span_ids = {span.span_id for span in spans}
            self.assertTrue(
                all(span.parent_span_id in span_ids for span in spans if span.parent_span_id)
            )
            incidents, _ = trace_store.list_incidents()
            self.assertEqual(incidents[0].status, SpanStatus.SUCCEEDED)
            self.assertEqual(incidents[0].risk_level, "high")


if __name__ == "__main__":
    unittest.main()
