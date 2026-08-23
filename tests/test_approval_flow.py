"""Tests for enterprise risk, approval, and allowlisted execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from network_agent_rag.packs.networkops.agents.enterprise import (
    AllowlistedExecutor,
    ApprovalConflict,
    evaluate_risk,
    execute_actions,
    validate_approval,
)
from network_agent_rag.packs.networkops.api.enterprise import create_enterprise_app
from network_agent_rag.audit import SQLiteAuditLog
from tests.test_checkpoint import build_graph


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def action(action_id: str, tool_name: str = "digital_twin.update_link") -> dict[str, object]:
    return {
        "action_id": action_id,
        "tool_name": tool_name,
        "target": "SW1--SW2",
        "arguments": {"status": "up"},
        "verification_steps": ["verify packet loss"],
        "rollback_instructions": "restore previous link status",
    }


def state(*, risk_level: str = "high", approval: bool = True) -> dict[str, object]:
    return {
        "incident_id": "INC-1001",
        "repair_plan": {
            "risk_level": risk_level,
            "requires_human_approval": approval,
            "steps": ["repair link"],
            "verification_steps": ["verify link"],
            "rollback_conditions": ["rollback on failure"],
            "execution_status": "not_executed",
        },
        "proposed_actions": [action("ACTION-1")],
        "approval_result": None,
    }


class ApprovalFlowTests(unittest.TestCase):
    def test_high_risk_and_forced_approval_pause(self) -> None:
        high = evaluate_risk(state(), now=NOW, approval_ttl_seconds=1800)
        forced = evaluate_risk(
            state(risk_level="low", approval=True),
            now=NOW,
            approval_ttl_seconds=1800,
        )
        low = evaluate_risk(
            state(risk_level="low", approval=False),
            now=NOW,
            approval_ttl_seconds=1800,
        )

        self.assertTrue(high["approval_required"])
        self.assertTrue(forced["approval_required"])
        self.assertFalse(low["approval_required"])
        self.assertEqual(high["expires_at"], NOW + timedelta(minutes=30))
        self.assertEqual(high["plan_digest"], forced["plan_digest"])

    def test_approval_is_bound_to_digest_and_expiry(self) -> None:
        current = state()
        current["risk_decision"] = evaluate_risk(
            current, now=NOW, approval_ttl_seconds=1800
        )
        approved = validate_approval(
            current,
            {
                "decision": "approve",
                "actor": "noc-operator",
                "plan_digest": current["risk_decision"]["plan_digest"],
                "comment": "maintenance window open",
            },
            now=NOW + timedelta(minutes=5),
        )

        self.assertEqual(approved["decision"], "approve")
        self.assertEqual(approved["actor"], "noc-operator")
        with self.assertRaisesRegex(ApprovalConflict, "digest"):
            validate_approval(
                current,
                {
                    "decision": "approve",
                    "actor": "noc-operator",
                    "plan_digest": "stale",
                },
                now=NOW,
            )
        with self.assertRaisesRegex(ApprovalConflict, "expired"):
            validate_approval(
                current,
                {
                    "decision": "approve",
                    "actor": "noc-operator",
                    "plan_digest": current["risk_decision"]["plan_digest"],
                },
                now=NOW + timedelta(minutes=31),
            )

    def test_reject_never_calls_executor(self) -> None:
        calls: list[str] = []
        current = state()
        current["approval_result"] = {
            "decision": "reject",
            "actor": "noc-operator",
            "plan_digest": "digest",
            "comment": None,
            "decided_at": NOW,
        }
        executor = AllowlistedExecutor(
            {
                "digital_twin.update_link": lambda item: calls.append(
                    item["action_id"]
                )
                or {"status": "succeeded", "message": "ok"}
            }
        )

        result = execute_actions(current, executor)

        self.assertEqual(result["status"], "not_executed")
        self.assertEqual(result["error_code"], "APPROVAL_REJECTED")
        self.assertEqual(calls, [])

    def test_missing_executor_blocks_without_changes(self) -> None:
        current = state(risk_level="low", approval=False)
        current["risk_decision"] = evaluate_risk(
            current, now=NOW, approval_ttl_seconds=1800
        )

        result = execute_actions(current, None)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_code"], "EXECUTOR_NOT_CONFIGURED")

    def test_allowlist_stops_after_first_failed_action_without_retry(self) -> None:
        calls: list[str] = []

        def fail(item: dict[str, object]) -> dict[str, object]:
            calls.append(item["action_id"])
            raise ConnectionError("device unavailable")

        current = state(risk_level="low", approval=False)
        current["proposed_actions"] = [action("ACTION-1"), action("ACTION-2")]
        current["risk_decision"] = evaluate_risk(
            current, now=NOW, approval_ttl_seconds=1800
        )

        result = execute_actions(
            current,
            AllowlistedExecutor({"digital_twin.update_link": fail}),
        )

        self.assertEqual(calls, ["ACTION-1"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["actions"]), 1)

    def test_executor_error_does_not_expose_exception_secrets(self) -> None:
        current = state(risk_level="low", approval=False)
        current["risk_decision"] = evaluate_risk(
            current, now=NOW, approval_ttl_seconds=1800
        )
        executor = AllowlistedExecutor(
            {
                "digital_twin.update_link": lambda item: (_ for _ in ()).throw(
                    RuntimeError("token=super-secret")
                )
            }
        )

        result = execute_actions(current, executor)

        self.assertNotIn("super-secret", result["actions"][0]["message"])
        self.assertIn("RuntimeError", result["actions"][0]["message"])

    def test_incident_api_streams_pause_resume_and_status(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(InMemorySaver(), audit, calls)
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-1001",
                        "query": "分析 SW1 到 SW2 丢包",
                    },
                )
                events = _events(started.text)
                approval = next(data for name, data in events if name == "approval_required")

                self.assertEqual(started.status_code, 200)
                self.assertEqual(events[0][0], "start")
                self.assertNotIn("answer", [name for name, _ in events])
                self.assertEqual(calls, [])

                duplicate = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-1001",
                        "query": "duplicate",
                    },
                )
                self.assertEqual(duplicate.status_code, 409)

                resumed = client.post(
                    "/api/v1/incidents/INC-1001/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )
                resumed_events = _events(resumed.text)
                answer = next(data for name, data in resumed_events if name == "answer")
                status = client.get("/api/v1/incidents/INC-1001").json()
                audit_events = client.get("/api/v1/incidents/INC-1001/audit").json()

                self.assertEqual(resumed.status_code, 200)
                self.assertEqual(calls, ["ACTION-1"])
                self.assertEqual(answer["enterprise_status"], "executed")
                self.assertEqual(status["enterprise_status"], "executed")
                self.assertEqual(
                    {event["event_type"] for event in audit_events["events"]},
                    {"agent_call", "tool_call", "decision", "approval"},
                )

    def test_incident_api_rejects_stale_or_unknown_approval(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            graph = build_graph(InMemorySaver(), audit, [])
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                clock=lambda: NOW,
            )
            with TestClient(app) as client:
                missing = client.post(
                    "/api/v1/incidents/UNKNOWN/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": "0" * 64,
                    },
                )
                client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-1001",
                        "query": "分析丢包",
                    },
                )
                stale = client.post(
                    "/api/v1/incidents/INC-1001/approval",
                    json={
                        "decision": "approve",
                        "actor": "noc-operator",
                        "plan_digest": "0" * 64,
                    },
                )

                self.assertEqual(missing.status_code, 404)
                self.assertEqual(stale.status_code, 409)

    def test_incident_api_reject_decision_resumes_without_execution(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(InMemorySaver(), audit, calls)
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                clock=lambda: NOW,
            )
            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-REJECT",
                        "query": "分析丢包",
                    },
                )
                digest = next(
                    data["plan_digest"]
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )

                rejected = client.post(
                    "/api/v1/incidents/INC-REJECT/approval",
                    json={
                        "decision": "reject",
                        "actor": "noc-operator",
                        "plan_digest": digest,
                        "comment": "change window closed",
                    },
                )
                answer = next(
                    data
                    for name, data in _events(rejected.text)
                    if name == "answer"
                )

                self.assertEqual(answer["enterprise_status"], "rejected")
                self.assertIsNone(answer["execution_result"])
                self.assertIn("未执行任何网络变更", answer["answer"])
                self.assertEqual(calls, [])


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events


if __name__ == "__main__":
    unittest.main()
