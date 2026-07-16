"""Audit-backed TraceEvent model and collector tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from network_agent_rag.api.enterprise import create_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.observability import (
    TraceCollector,
    TraceEvent,
    TraceEventStatus,
    TraceEventType,
    load_trace_events,
    SQLiteTraceStore,
)
from tests.test_approval_flow import _events
from tests.test_checkpoint import build_graph
from tests.test_multi_agent_workflow import topology


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(milliseconds=25)
        return value


class TraceEventModelTests(unittest.TestCase):
    def test_accepts_strict_timezone_aware_event(self) -> None:
        event = TraceEvent(
            timestamp=NOW.astimezone(timezone(timedelta(hours=8))),
            incident_id=" INC-1 ",
            trace_id=" trace-1 ",
            run_id=" run-1 ",
            agent_name=" DiagnosisAgent ",
            node_name=" DiagnosisAgent ",
            event_type="agent_enter",
            input={"summary": {}, "sha256": "a" * 64},
            output=None,
            latency=None,
            status="running",
        )

        self.assertEqual(event.incident_id, "INC-1")
        self.assertEqual(event.timestamp.utcoffset(), timedelta(0))
        self.assertEqual(event.event_type, TraceEventType.AGENT_ENTER)
        self.assertEqual(event.status, TraceEventStatus.RUNNING)

    def test_rejects_naive_time_invalid_values_and_extra_fields(self) -> None:
        payload = {
            "timestamp": NOW,
            "incident_id": "INC-1",
            "trace_id": "trace-1",
            "run_id": "run-1",
            "agent_name": "DiagnosisAgent",
            "node_name": "DiagnosisAgent",
            "event_type": "agent_exit",
            "input": None,
            "output": None,
            "latency": 1.0,
            "status": "succeeded",
        }
        for field, value in (
            ("timestamp", datetime(2026, 7, 16, 12, 0)),
            ("incident_id", " "),
            ("event_type", "unknown"),
            ("latency", -1.0),
            ("status", "unknown"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                TraceEvent.model_validate({**payload, field: value})
        with self.assertRaises(ValidationError):
            TraceEvent.model_validate({**payload, "unexpected": True})
        with self.assertRaises(ValidationError):
            TraceEvent.model_validate(
                {**payload, "input": {"prompt": "must not be persisted"}}
            )
        for unsafe_summary in (
            {"prompt": "full secret prompt"},
            {"target": "document body copied into a repair target"},
            {"error_type": "X" * 129},
            {"tool_name": 123},
            {"document_count": 1.5},
            {"relevance_score": 2.0},
        ):
            with self.subTest(summary=unsafe_summary), self.assertRaises(ValidationError):
                TraceEvent.model_validate(
                    {
                        **payload,
                        "input": {
                            "summary": unsafe_summary,
                            "sha256": "a" * 64,
                        },
                    }
                )


class TraceCollectorTests(unittest.TestCase):
    def test_records_paired_agent_events_in_audit_order(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())

            token = collector.agent_enter(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="DiagnosisAgent",
                node_name="DiagnosisAgent",
                input_data={"prompt": "private query", "document_count": 3},
                attempt=1,
            )
            collector.agent_exit(
                token,
                status=TraceEventStatus.SUCCEEDED,
                output_data={"answer": "private answer", "relevance_score": 0.91},
            )

            events = load_trace_events(audit, "INC-1")
            audit_events = audit.list_events("INC-1", event_type=AuditEventType.TRACE)

            self.assertEqual(
                [event.event_type for event in events],
                [TraceEventType.AGENT_ENTER, TraceEventType.AGENT_EXIT],
            )
            self.assertEqual(events[1].latency, 25.0)
            self.assertEqual(len(audit_events), 2)
            encoded = json.dumps([event.model_dump(mode="json") for event in events])
            self.assertNotIn("private query", encoded)
            self.assertNotIn("private answer", encoded)
            self.assertEqual(events[0].input["summary"]["document_count"], 3)
            self.assertEqual(events[1].output["summary"]["relevance_score"], 0.91)
            self.assertEqual(len(events[0].input["sha256"]), 64)

    def test_records_calls_pause_resume_and_repair_once(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())

            result = collector.record_call(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="DiagnosisAgent",
                node_name="search_knowledge",
                event_type=TraceEventType.RAG_RETRIEVAL,
                operation=lambda: ["document-body"],
                input_data={"query": "private query"},
                output_builder=lambda value: {"document_count": len(value)},
                attempt=1,
            )
            collector.approval_pause(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="Approval",
                node_name="Approval",
                input_data={"risk_level": "high"},
                attempt=1,
            )
            collector.resume(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-2",
                agent_name="EnterpriseWorkflow",
                node_name="Approval",
                input_data={"decision": "approve", "comment": "private note"},
                attempt=1,
            )
            collector.repair_execute(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-2",
                agent_name="Execute",
                node_name="Execute",
                input_data={
                    "action_count": 1,
                    "tool_name": "safe_tool",
                    "target": "full private prompt copied as target",
                },
                attempt=1,
            )
            collector.repair_execute(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-2",
                agent_name="Execute",
                node_name="Execute",
                input_data={
                    "action_count": 1,
                    "tool_name": "safe_tool",
                    "target": "full private prompt copied as target",
                },
                attempt=1,
            )

            events = load_trace_events(audit, "INC-1")

            self.assertEqual(result, ["document-body"])
            self.assertEqual(
                [event.event_type for event in events],
                [
                    TraceEventType.RAG_RETRIEVAL,
                    TraceEventType.APPROVAL_PAUSE,
                    TraceEventType.RESUME,
                    TraceEventType.REPAIR_EXECUTE,
                ],
            )
            self.assertNotIn("private query", json.dumps(events, default=str))
            self.assertNotIn("private note", json.dumps(events, default=str))
            self.assertNotIn("full private prompt", json.dumps(events, default=str))
            self.assertEqual(len(events[-1].input["summary"]["target_sha256"]), 64)

    def test_failed_call_records_error_type_and_reraises(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())

            def fail() -> None:
                raise TimeoutError("secret failure text")

            with self.assertRaises(TimeoutError):
                collector.record_call(
                    incident_id="INC-1",
                    trace_id="trace-1",
                    run_id="run-1",
                    agent_name="TopologyAgent",
                    node_name="query_topology",
                    event_type=TraceEventType.TOOL_CALL,
                    operation=fail,
                    attempt=1,
                )

            event = load_trace_events(audit, "INC-1")[0]
            self.assertEqual(event.status, TraceEventStatus.FAILED)
            self.assertEqual(event.output["summary"]["error_type"], "TimeoutError")
            self.assertNotIn("secret failure text", json.dumps(event.model_dump(mode="json")))

    def test_hash_distinguishes_redacted_payloads_without_storing_them(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())
            for attempt, query in enumerate(("first private query", "second private query"), 1):
                collector.record_call(
                    incident_id="INC-1",
                    trace_id="trace-1",
                    run_id="run-1",
                    agent_name="DiagnosisAgent",
                    node_name="search_knowledge",
                    event_type=TraceEventType.RAG_RETRIEVAL,
                    operation=list,
                    input_data={"query": query},
                    output_builder=lambda value: {"document_count": len(value)},
                    attempt=attempt,
                )

            events = load_trace_events(audit, "INC-1")
            encoded = json.dumps([event.model_dump(mode="json") for event in events])
            self.assertNotEqual(events[0].input["sha256"], events[1].input["sha256"])
            self.assertNotIn("first private query", encoded)
            self.assertNotIn("second private query", encoded)

    def test_nonconforming_metadata_is_hashed_instead_of_breaking_calls(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())

            result = collector.record_call(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="DiagnosisAgent",
                node_name="query_metrics",
                event_type=TraceEventType.TOOL_CALL,
                operation=lambda: {"status": "partial success"},
                output_builder=lambda value: value,
            )
            collector.repair_execute(
                incident_id="INC-1",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="Execute",
                node_name="Execute",
                input_data={"tool_name": "netbox/device.update"},
            )

            events = load_trace_events(audit, "INC-1")
            encoded = json.dumps([event.model_dump(mode="json") for event in events])
            self.assertEqual(result, {"status": "partial success"})
            self.assertNotIn("partial success", encoded)
            self.assertNotIn("netbox/device.update", encoded)
            self.assertTrue(all(event.input or event.output for event in events))


class EnterpriseTraceEventIntegrationTests(unittest.TestCase):
    def test_transient_tool_retry_records_failed_and_succeeded_attempts(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit)
            callback_calls = 0

            def transient_topology(plan):
                nonlocal callback_calls
                callback_calls += 1
                if callback_calls == 1:
                    raise TimeoutError("temporary topology outage")
                return topology(plan)

            graph = build_graph(
                InMemorySaver(),
                audit,
                [],
                None,
                collector,
                retrieve_topology=transient_topology,
                clock=lambda: NOW,
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-TRACE-RETRY",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )

            self.assertEqual(response.status_code, 200)
            tool_events = [
                event
                for event in load_trace_events(audit, "INC-TRACE-RETRY")
                if event.node_name == "query_topology"
            ]
            self.assertEqual(callback_calls, 2)
            self.assertEqual(
                [event.status for event in tool_events],
                [TraceEventStatus.FAILED, TraceEventStatus.SUCCEEDED],
            )
            audit_events = audit.list_events(
                "INC-TRACE-RETRY", event_type=AuditEventType.TRACE
            )
            retry_keys = [
                event.idempotency_key
                for event in audit_events
                if event.action == TraceEventType.TOOL_CALL.value
                and "query_topology" in (event.idempotency_key or "")
            ]
            self.assertTrue(retry_keys[0].endswith(":1"))
            self.assertTrue(retry_keys[1].endswith(":2"))

    def test_collector_only_resume_preserves_trace_id(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit)
            graph = build_graph(
                InMemorySaver(), audit, [], None, collector, clock=lambda: NOW
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-COLLECTOR-ONLY",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                client.post(
                    "/api/v1/incidents/INC-COLLECTOR-ONLY/approval",
                    json={
                        "decision": "reject",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

            events = load_trace_events(audit, "INC-COLLECTOR-ONLY")
            self.assertEqual(len({event.trace_id for event in events}), 1)
            self.assertEqual(len({event.run_id for event in events}), 2)

    def test_workflow_records_nodes_tools_rag_pause_resume_and_execute(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            collector = TraceCollector(audit)
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                trace_store,
                collector,
                clock=lambda: NOW,
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_store=trace_store,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-TRACE-EVENTS",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                resumed = client.post(
                    "/api/v1/incidents/INC-TRACE-EVENTS/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

            self.assertEqual(resumed.status_code, 200)
            events = load_trace_events(audit, "INC-TRACE-EVENTS")
            kinds = [event.event_type for event in events]
            self.assertIn(TraceEventType.TOOL_CALL, kinds)
            self.assertIn(TraceEventType.RAG_RETRIEVAL, kinds)
            self.assertIn(TraceEventType.APPROVAL_PAUSE, kinds)
            self.assertIn(TraceEventType.RESUME, kinds)
            self.assertEqual(kinds.count(TraceEventType.REPAIR_EXECUTE), 1)
            self.assertEqual(calls, ["ACTION-1"])
            traces = {event.trace_id for event in events}
            runs = {event.run_id for event in events}
            self.assertEqual(len(traces), 1)
            self.assertEqual(len(runs), 2)
            for node_name in (
                "Supervisor",
                "TopologyAgent",
                "LogAgent",
                "DiagnosisAgent",
                "RepairAgent",
                "RiskCheck",
                "ReportAgent",
            ):
                node_events = [
                    event.event_type
                    for event in events
                    if event.node_name == node_name
                ]
                self.assertIn(TraceEventType.AGENT_ENTER, node_events)
                self.assertIn(TraceEventType.AGENT_EXIT, node_events)

    def test_reject_does_not_record_repair_execute(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            collector = TraceCollector(audit)
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                trace_store,
                collector,
                clock=lambda: NOW,
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_store=trace_store,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-REJECT-TRACE",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                client.post(
                    "/api/v1/incidents/INC-REJECT-TRACE/approval",
                    json={
                        "decision": "reject",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

            kinds = [
                event.event_type
                for event in load_trace_events(audit, "INC-REJECT-TRACE")
            ]
            self.assertNotIn(TraceEventType.REPAIR_EXECUTE, kinds)
            self.assertEqual(calls, [])

    def test_trace_persistence_failure_blocks_execution(self) -> None:
        class FailingAudit(SQLiteAuditLog):
            def record(self, **values):
                if (
                    values.get("event_type") == AuditEventType.TRACE
                    and values.get("action") == TraceEventType.REPAIR_EXECUTE.value
                ):
                    raise OSError("audit unavailable")
                return super().record(**values)

        with TemporaryDirectory() as directory:
            audit = FailingAudit(Path(directory) / "audit.sqlite3")
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            collector = TraceCollector(audit)
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                trace_store,
                collector,
                clock=lambda: NOW,
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_store=trace_store,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-TRACE-FAIL",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                resumed = client.post(
                    "/api/v1/incidents/INC-TRACE-FAIL/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

            error = next(data for name, data in _events(resumed.text) if name == "error")
            self.assertEqual(error["code"], "TRACE_PERSISTENCE_FAILED")
            self.assertEqual(calls, [])

    def test_trace_persistence_recovery_can_retry_approved_execution(self) -> None:
        class FailOnceAudit(SQLiteAuditLog):
            failed = False

            def record(self, **values):
                if (
                    not self.failed
                    and values.get("event_type") == AuditEventType.TRACE
                    and values.get("action") == TraceEventType.REPAIR_EXECUTE.value
                ):
                    self.failed = True
                    raise OSError("audit unavailable once")
                return super().record(**values)

        with TemporaryDirectory() as directory:
            audit = FailOnceAudit(Path(directory) / "audit.sqlite3")
            trace_store = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            collector = TraceCollector(audit)
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                trace_store,
                collector,
                clock=lambda: NOW,
            )
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                trace_store=trace_store,
                trace_collector=collector,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-TRACE-RECOVER",
                        "query": "analyze SW1 to SW2 packet loss",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                decision = {
                    "decision": "approve",
                    "actor": "noc-operator",
                    "plan_digest": approval["plan_digest"],
                }
                failed = client.post(
                    "/api/v1/incidents/INC-TRACE-RECOVER/approval",
                    json=decision,
                )
                status = client.get("/api/v1/incidents/INC-TRACE-RECOVER")
                recovered = client.post(
                    "/api/v1/incidents/INC-TRACE-RECOVER/approval",
                    json=decision,
                )

            failed_error = next(
                data for name, data in _events(failed.text) if name == "error"
            )
            self.assertEqual(failed_error["code"], "TRACE_PERSISTENCE_FAILED")
            self.assertEqual(status.json()["enterprise_status"], "approved")
            self.assertEqual(recovered.status_code, 200)
            self.assertTrue(any(name == "answer" for name, _ in _events(recovered.text)))
            self.assertEqual(calls, ["ACTION-1"])
            events = load_trace_events(audit, "INC-TRACE-RECOVER")
            self.assertEqual(len({event.trace_id for event in events}), 1)
            self.assertEqual(len({event.run_id for event in events}), 3)
            resume_events = [
                event for event in events if event.event_type == TraceEventType.RESUME
            ]
            self.assertEqual(len(resume_events), 2)
            self.assertEqual(
                resume_events[-1].input["summary"]["status"],
                "execution_retry",
            )


class EnterpriseTraceEventApiTests(unittest.TestCase):
    def test_returns_audit_backed_trace_chain(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            collector = TraceCollector(audit, clock=Clock())
            collector.approval_pause(
                incident_id="INC-API-TRACE",
                trace_id="trace-1",
                run_id="run-1",
                agent_name="Approval",
                node_name="Approval",
                input_data={"risk_level": "high"},
                attempt=1,
            )
            app = create_enterprise_app(audit_log=audit)

            with TestClient(app) as client:
                response = client.get(
                    "/api/v1/enterprise/incidents/INC-API-TRACE/trace"
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["incident_id"], "INC-API-TRACE")
            self.assertEqual(len(payload["events"]), 1)
            self.assertEqual(payload["events"][0]["event_type"], "approval_pause")

    def test_reports_missing_invalid_and_unconfigured_trace(self) -> None:
        with TemporaryDirectory() as directory:
            configured = create_enterprise_app(
                audit_log=SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            )
            unconfigured = create_enterprise_app()

            with TestClient(configured) as client:
                missing = client.get(
                    "/api/v1/enterprise/incidents/UNKNOWN/trace"
                )
                invalid = client.get(
                    "/api/v1/enterprise/incidents/invalid%20id/trace"
                )
            with TestClient(unconfigured) as client:
                unavailable = client.get(
                    "/api/v1/enterprise/incidents/INC-1/trace"
                )

            self.assertEqual(missing.status_code, 404)
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(unavailable.status_code, 503)


if __name__ == "__main__":
    unittest.main()
