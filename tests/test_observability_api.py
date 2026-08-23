"""Metrics, timeline, and observability API tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from network_agent_rag.packs.networkops.api.enterprise import create_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.observability import (
    IncidentTimelineBuilder,
    MetricsService,
    SQLiteTraceStore,
    SpanKind,
    SpanStatus,
)


NOW = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)


def traced_incident(store: SQLiteTraceStore, incident_id: str, status: SpanStatus) -> None:
    workflow = store.start_span(
        trace_id=f"trace-{incident_id}",
        run_id=f"run-{incident_id}",
        incident_id=incident_id,
        kind=SpanKind.WORKFLOW,
        name="EnterpriseWorkflow",
        started_at=NOW,
        attributes={
            "risk_level": "high",
            "approval_decision": "approve",
            "execution_status": "succeeded",
        },
    )
    agent = store.start_span(
        trace_id=f"trace-{incident_id}",
        run_id=f"run-{incident_id}",
        incident_id=incident_id,
        parent_span_id=workflow.span_id,
        kind=SpanKind.AGENT,
        name="DiagnosisAgent",
        started_at=NOW + timedelta(seconds=1),
    )
    tool = store.start_span(
        trace_id=f"trace-{incident_id}",
        run_id=f"run-{incident_id}",
        incident_id=incident_id,
        parent_span_id=agent.span_id,
        kind=SpanKind.TOOL,
        name="search_knowledge",
        started_at=NOW + timedelta(seconds=1, milliseconds=100),
    )
    store.finish_span(
        tool.span_id,
        status=SpanStatus.FAILED,
        ended_at=NOW + timedelta(seconds=1, milliseconds=500),
    )
    store.finish_span(
        agent.span_id,
        status=SpanStatus.SUCCEEDED,
        ended_at=NOW + timedelta(seconds=2),
        attributes={
            "relevance_score": 0.9,
            "diagnosis_iteration": 2,
            "evidence_count": 4,
        },
    )
    store.finish_span(
        workflow.span_id,
        status=status,
        ended_at=NOW + timedelta(seconds=3),
    )


class MetricsAndTimelineTests(unittest.TestCase):
    def test_metrics_are_low_cardinality_and_prometheus_compatible(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            traced_incident(store, "INC-SECRET-123", SpanStatus.INTERRUPTED)
            service = MetricsService(store)

            summary = service.summary()
            prometheus = service.render_prometheus()

            self.assertEqual(summary["incidents_total"], 1)
            self.assertEqual(summary["pending_approvals"], 1)
            self.assertEqual(summary["spans_total"], 3)
            self.assertEqual(summary["spans_by_status"]["succeeded"], 1)
            self.assertEqual(summary["agent_calls_by_status"], {"succeeded": 1})
            self.assertEqual(summary["tool_calls_by_status"], {"failed": 1})
            self.assertEqual(summary["risk_levels"], {"high": 1})
            self.assertEqual(summary["approval_decisions"], {"approve": 1})
            self.assertEqual(summary["execution_results"], {"succeeded": 1})
            self.assertEqual(summary["rag_quality"]["relevance_score_average"], 0.9)
            self.assertEqual(summary["rag_quality"]["quality_iterations_average"], 2.0)
            self.assertEqual(summary["rag_quality"]["evidence_count_average"], 4.0)
            self.assertEqual(summary["incident_trend"][0]["total"], 1)
            self.assertEqual(summary["incident_trend"][0]["interrupted"], 1)
            self.assertIn('networkops_incidents_total{status="interrupted"} 1', prometheus)
            self.assertIn("networkops_span_duration_ms_sum", prometheus)
            self.assertIn('networkops_incident_risk_total{risk_level="high"} 1', prometheus)
            self.assertIn('networkops_approval_decisions_total{decision="approve"} 1', prometheus)
            self.assertIn("networkops_rag_relevance_score_average 0.9", prometheus)
            self.assertNotIn("INC-SECRET-123", prometheus)

    def test_timeline_merges_trace_and_audit_in_stable_utc_order(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            traced_incident(store, "INC-1", SpanStatus.INTERRUPTED)
            audit.record(
                incident_id="INC-1",
                event_type=AuditEventType.APPROVAL,
                actor="noc-operator",
                action="decide",
                outcome="approve",
            )

            timeline = IncidentTimelineBuilder(store, audit).build("INC-1")

            self.assertTrue(
                any(
                    item.event_type == "span_started"
                    and item.name == "EnterpriseWorkflow"
                    for item in timeline
                )
            )
            self.assertTrue(any(item.source == "audit" for item in timeline))
            self.assertTrue(all(item.timestamp.tzinfo is not None for item in timeline))
            self.assertEqual(timeline, sorted(timeline, key=lambda item: (item.timestamp, item.event_id)))


class ObservabilityApiTests(unittest.TestCase):
    def test_exposes_incident_trace_timeline_metrics_and_cursor_listing(self) -> None:
        with TemporaryDirectory() as directory:
            trace_store = SQLiteTraceStore(Path(directory) / "traces.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            traced_incident(trace_store, "INC-1", SpanStatus.INTERRUPTED)
            traced_incident(trace_store, "INC-2", SpanStatus.SUCCEEDED)
            app = create_enterprise_app(audit_log=audit, trace_store=trace_store)

            with TestClient(app) as client:
                first_page = client.get(
                    "/api/v1/observability/incidents", params={"limit": 1}
                )
                cursor = first_page.json()["next_cursor"]
                second_page = client.get(
                    "/api/v1/observability/incidents",
                    params={"limit": 1, "cursor": cursor},
                )
                filtered = client.get(
                    "/api/v1/observability/incidents", params={"status": "interrupted"}
                )
                trace = client.get("/api/v1/observability/incidents/INC-1/trace")
                trace_page = client.get(
                    "/api/v1/observability/incidents/INC-1/trace",
                    params={"limit": 1},
                )
                timeline = client.get("/api/v1/observability/incidents/INC-1/timeline")
                timeline_page = client.get(
                    "/api/v1/observability/incidents/INC-1/timeline",
                    params={"limit": 1},
                )
                metrics = client.get("/api/v1/observability/metrics/summary")
                prometheus = client.get("/metrics")
                missing = client.get("/api/v1/observability/incidents/UNKNOWN/trace")
                invalid_cursor = client.get(
                    "/api/v1/observability/incidents", params={"cursor": "bad"}
                )

            self.assertEqual(first_page.status_code, 200)
            self.assertEqual(len(first_page.json()["items"]), 1)
            self.assertEqual(len(second_page.json()["items"]), 1)
            self.assertEqual(filtered.json()["items"][0]["incident_id"], "INC-1")
            self.assertEqual(trace.status_code, 200)
            self.assertEqual(len(trace.json()["spans"]), 3)
            self.assertEqual(len(trace_page.json()["spans"]), 1)
            self.assertEqual(trace_page.json()["next_cursor"], "1")
            self.assertEqual(timeline.status_code, 200)
            self.assertEqual(len(timeline_page.json()["events"]), 1)
            self.assertEqual(timeline_page.json()["next_cursor"], "1")
            self.assertEqual(metrics.json()["incidents_total"], 2)
            self.assertTrue(prometheus.headers["content-type"].startswith("text/plain"))
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(invalid_cursor.status_code, 422)


if __name__ == "__main__":
    unittest.main()
