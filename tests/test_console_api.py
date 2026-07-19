"""Read-only Operator Console projection API tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from network_agent_rag.api.enterprise import create_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.auth import JWTProvider, JWTTokenManager, Role, UserIdentity
from network_agent_rag.evaluation import (
    BenchmarkReport,
    BenchmarkCase,
    BenchmarkResultStore,
    BenchmarkRunner,
    EvaluationMetricsSnapshot,
)
from network_agent_rag.observability import (
    SQLiteMetricsStore,
    SQLiteTraceStore,
    SpanKind,
    SpanStatus,
)


NOW = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
SECRET = "c" * 32


class ReadOnlyWorkflow:
    def __init__(self, states: dict[str, dict[str, object]]) -> None:
        self.states = states
        self.reads = 0

    async def aget_state(self, config: dict[str, dict[str, object]]) -> object:
        self.reads += 1
        incident_id = str(config["configurable"]["thread_id"])
        return SimpleNamespace(values=self.states.get(incident_id, {}), tasks=())


class EmptyEvaluationProvider:
    def list_reports(self) -> tuple[object, ...]:
        return ()


class StaticEvaluationProvider:
    def __init__(self, report: BenchmarkReport) -> None:
        self.report = report

    def list_reports(self) -> tuple[BenchmarkReport, ...]:
        return (self.report,)


def _identity(role: Role = Role.OPERATOR) -> UserIdentity:
    return UserIdentity(
        user_id="console-user",
        username="console-operator",
        roles=(role,),
    )


def _headers(role: Role = Role.OPERATOR) -> dict[str, str]:
    manager = JWTTokenManager(SECRET, clock=lambda: NOW)
    return {"Authorization": f"Bearer {manager.create_token(_identity(role))}"}


def _benchmark_store(path: Path) -> BenchmarkResultStore:
    store = BenchmarkResultStore(path)
    case = BenchmarkCase(
        case_id="CASE-1",
        query="diagnose connectivity",
        expected_route=["diagnosis"],
        required_evidence_refs=["evidence:1"],
        expected_root_cause="link failure",
        confidence_min=70,
        confidence_max=90,
        approval_required=False,
        expected_execution_status="not_executed",
    )
    runner = BenchmarkRunner(
        lambda _: {
            "route": ["diagnosis"],
            "evidence_refs": ["evidence:1"],
            "root_cause": "link failure",
            "confidence_percent": 80,
            "approval_required": False,
            "execution_status": "not_executed",
            "grounded_claims": 1,
            "total_claims": 1,
            "unsafe_execution_claims": 0,
            "duration_ms": 25,
            "quality_iterations": 1,
        },
        clock=lambda: NOW,
        run_id_factory=lambda: "run-console",
    )
    store.save(
        runner.run(
            [case], dataset_name="network-cases", dataset_version="v1"
        )
    )
    return store


def build_console_fixture(directory: str) -> tuple[object, dict[str, str], object, object, object]:
    root = Path(directory)
    audit = SQLiteAuditLog(root / "audit.sqlite3")
    trace = SQLiteTraceStore(root / "trace.sqlite3")
    workflow = ReadOnlyWorkflow(
        {
            "INC-1": {
                "enterprise_status": "executed",
                "current_agent": "report",
                "risk_decision": {
                    "risk_level": "high",
                    "approval_required": True,
                    "prompt": "must never escape",
                },
                "approval_result": {
                    "decision": "approve",
                    "comment": "credential=must-not-escape",
                },
                "execution_result": {
                    "status": "succeeded",
                    "message": "secret must-not-escape",
                },
            }
        }
    )
    workflow_span = trace.start_span(
        trace_id="trace-1",
        run_id="run-1",
        incident_id="INC-1",
        kind=SpanKind.WORKFLOW,
        name="EnterpriseWorkflow",
        started_at=NOW,
        attributes={"risk_level": "high", "prompt": "must-not-escape"},
    )
    trace.finish_span(
        workflow_span.span_id,
        status=SpanStatus.SUCCEEDED,
        ended_at=NOW + timedelta(milliseconds=100),
    )
    agent_span = trace.start_span(
        trace_id="trace-1",
        run_id="run-1",
        incident_id="INC-1",
        kind=SpanKind.AGENT,
        name="DiagnosisAgent",
        parent_span_id=workflow_span.span_id,
        started_at=NOW + timedelta(milliseconds=10),
        attributes={"password": "must-not-escape"},
    )
    trace.finish_span(
        agent_span.span_id,
        status=SpanStatus.SUCCEEDED,
        ended_at=NOW + timedelta(milliseconds=40),
    )
    audit.record(
        incident_id="INC-1",
        event_type=AuditEventType.DECISION,
        actor="RiskCheck",
        action="risk_assessment_created",
        outcome="created",
        details={"risk_level": "high", "prompt": "must-not-escape"},
    )
    audit.record(
        incident_id="INC-1",
        event_type=AuditEventType.DECISION,
        actor="Policy",
        action="policy_denied",
        outcome="denied",
        details={
            "policy_id": "a" * 64,
            "decision": "DENY",
            "operation": "restart_device",
            "risk_level": "high",
            "devices": ["secret-device"],
        },
    )
    benchmark = _benchmark_store(root / "benchmarks")
    manager = JWTTokenManager(SECRET, clock=lambda: NOW)
    app = create_enterprise_app(
        agent_workflow=workflow,
        audit_log=audit,
        trace_store=trace,
        metrics_store=SQLiteMetricsStore(trace, audit),
        benchmark_store=benchmark,
        evaluation_report_provider=EmptyEvaluationProvider(),
        authentication_provider=JWTProvider(manager),
    )
    return app, _headers(), workflow, audit, trace


class ConsoleApiTests(unittest.TestCase):
    def test_projects_all_console_resources_without_raw_payloads(self) -> None:
        with TemporaryDirectory() as directory:
            app, headers, workflow, _, _ = build_console_fixture(directory)
            with TestClient(app) as client:
                dashboard = client.get("/api/v1/console/dashboard", headers=headers)
                incidents = client.get("/api/v1/console/incidents", headers=headers)
                detail = client.get("/api/v1/console/incidents/INC-1", headers=headers)
                trace = client.get(
                    "/api/v1/console/incidents/INC-1/trace", headers=headers
                )
                security = client.get(
                    "/api/v1/console/security-events",
                    params={"event_type": "policy_violation", "severity": "high"},
                    headers=headers,
                )
                policies = client.get(
                    "/api/v1/console/policy-decisions",
                    params={"decision": "DENY"},
                    headers=headers,
                )
                evaluations = client.get(
                    "/api/v1/console/evaluations", headers=headers
                )

            for response in (
                dashboard,
                incidents,
                detail,
                trace,
                security,
                policies,
                evaluations,
            ):
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(dashboard.json()["active_incidents"], 0)
            self.assertEqual(dashboard.json()["high_risk_operations"], 1)
            self.assertEqual(dashboard.json()["policy_denied_count"], 1)
            self.assertEqual(dashboard.json()["agent_success_rate"], 1.0)
            self.assertEqual(dashboard.json()["rca_accuracy"], 1.0)
            self.assertEqual(dashboard.json()["workflow_latency_ms"], 100.0)
            self.assertEqual(incidents.json()["items"][0]["incident_id"], "INC-1")
            self.assertEqual(detail.json()["approval_status"], "approve")
            self.assertEqual(detail.json()["execution_status"], "succeeded")
            self.assertEqual(workflow.reads, 1)
            self.assertNotIn("attributes", trace.text.lower())
            self.assertEqual(security.json()["items"][0]["event_type"], "policy_violation")
            self.assertTrue(
                policies.json()["items"][0]["policy_id"].startswith("policy-event-")
            )
            self.assertNotEqual(policies.json()["items"][0]["policy_id"], "a" * 64)
            self.assertEqual(evaluations.json()["items"][0]["dataset_version"], "v1")

    def test_pagination_filters_empty_data_and_errors(self) -> None:
        with TemporaryDirectory() as directory:
            app, headers, _, _, _ = build_console_fixture(directory)
            with TestClient(app) as client:
                page = client.get(
                    "/api/v1/console/incidents",
                    params={"status": "succeeded", "risk": "high", "limit": 1},
                    headers=headers,
                )
                empty = client.get(
                    "/api/v1/console/incidents",
                    params={"status": "failed"},
                    headers=headers,
                )
                bad_cursor = client.get(
                    "/api/v1/console/policy-decisions",
                    params={"cursor": "-1"},
                    headers=headers,
                )
                bad_limit = client.get(
                    "/api/v1/console/security-events",
                    params={"limit": 101},
                    headers=headers,
                )
                missing = client.get(
                    "/api/v1/console/incidents/UNKNOWN", headers=headers
                )
                missing_trace = client.get(
                    "/api/v1/console/incidents/UNKNOWN/trace", headers=headers
                )
                timeline_page = client.get(
                    "/api/v1/console/incidents/INC-1",
                    params={"limit": 1},
                    headers=headers,
                )
                invalid_policy = client.get(
                    "/api/v1/console/policy-decisions",
                    params={"decision": "maybe"},
                    headers=headers,
                )

            self.assertEqual(page.status_code, 200)
            self.assertEqual(len(page.json()["items"]), 1)
            self.assertEqual(empty.json(), {"items": [], "next_cursor": None})
            self.assertEqual(bad_cursor.status_code, 422)
            self.assertEqual(bad_limit.status_code, 422)
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing_trace.status_code, 404)
            self.assertEqual(len(timeline_page.json()["timeline"]), 1)
            self.assertIsNotNone(timeline_page.json()["timeline_next_cursor"])
            self.assertEqual(invalid_policy.status_code, 422)

    def test_missing_projection_stores_return_503_but_evaluations_are_empty(self) -> None:
        manager = JWTTokenManager(SECRET, clock=lambda: NOW)
        app = create_enterprise_app(authentication_provider=JWTProvider(manager))
        with TestClient(app) as client:
            incidents = client.get("/api/v1/console/incidents", headers=_headers())
            security = client.get("/api/v1/console/security-events", headers=_headers())
            evaluations = client.get("/api/v1/console/evaluations", headers=_headers())

        self.assertEqual(incidents.status_code, 503)
        self.assertEqual(security.status_code, 503)
        self.assertEqual(evaluations.status_code, 200)
        self.assertEqual(evaluations.json(), {"items": [], "next_cursor": None})

    def test_evaluations_include_optional_v2_projection_without_report_payload(self) -> None:
        metrics = EvaluationMetricsSnapshot(
            benchmark_count=1,
            total_cases=0,
            rca_accuracy=0.75,
            top1_accuracy=0.5,
            top3_accuracy=1.0,
            confidence_alignment=0.8,
            retrieval_hit_rate=1.0,
            context_precision=1.0,
            context_recall=1.0,
            repair_success_rate=0.0,
            policy_block_rate=0.0,
            blocked_rate=0.0,
            approval_rate=0.0,
            execution_safety_rate=1.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p95_latency=0.0,
            avg_tool_calls=0.0,
            failed_tool_calls=0,
            duplicate_calls=0,
        )
        report = BenchmarkReport(
            report_id="a" * 64,
            dataset_version="fault-v1",
            total_cases=0,
            passed_cases=0,
            failed_cases=0,
            metrics=metrics,
            results=(),
            created_at=NOW,
        )
        manager = JWTTokenManager(SECRET, clock=lambda: NOW)
        app = create_enterprise_app(
            evaluation_report_provider=StaticEvaluationProvider(report),
            authentication_provider=JWTProvider(manager),
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/console/evaluations", headers=_headers())

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["source"], "evaluation")
        self.assertEqual(item["top1_accuracy"], 0.5)
        self.assertNotIn("report_id", item)
        self.assertNotIn("results", item)


if __name__ == "__main__":
    unittest.main()
