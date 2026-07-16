"""API authorization boundary tests for enterprise incidents."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from starlette.requests import Request

from network_agent_rag.api.enterprise import create_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.auth import AuthorizationError, Permission, Role, User, UserContext
from network_agent_rag.auth.dependencies import (
    get_current_user_context,
    require_permission as require_api_permission,
)
from tests.test_checkpoint import NOW, build_graph


def _context(user_id: str, role: Role) -> UserContext:
    return UserContext(
        user=User(
            user_id=user_id,
            username=f"sensitive-{user_id}",
            roles=(role,),
        )
    )


def _events(payload: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    current = ""
    for line in payload.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: "):
            events.append((current, json.loads(line[6:])))
    return events


class ApiAuthorizationDependencyTests(unittest.TestCase):
    def test_dependency_api_is_available(self) -> None:
        self.assertIsNotNone(get_current_user_context)
        self.assertIsNotNone(require_api_permission)

    def test_dependency_returns_context_or_raises_typed_error(self) -> None:
        engineer = _context("engineer-1", Role.ENGINEER)
        create_plan = require_api_permission(Permission.CREATE_REPAIR_PLAN)
        approve = require_api_permission(Permission.APPROVE_REPAIR)
        app = create_enterprise_app()
        request = Request({"type": "http", "app": app, "path_params": {}})

        self.assertIs(create_plan(request, engineer), engineer)
        with self.assertRaises(AuthorizationError) as captured:
            approve(request, engineer)

        self.assertEqual(captured.exception.user_id, "engineer-1")
        self.assertIs(
            captured.exception.required_permission,
            Permission.APPROVE_REPAIR,
        )

    def test_dependency_enforces_engineer_admin_permission_matrix(self) -> None:
        app = create_enterprise_app()
        request = Request({"type": "http", "app": app, "path_params": {}})
        engineer = _context("engineer-1", Role.ENGINEER)
        admin = _context("admin-1", Role.ADMIN)

        allowed = (
            (engineer, Permission.CREATE_REPAIR_PLAN),
            (engineer, Permission.EXECUTE_REPAIR),
            (admin, Permission.APPROVE_REPAIR),
            (admin, Permission.MANAGE_SYSTEM),
        )
        denied = (
            (engineer, Permission.APPROVE_REPAIR),
            (admin, Permission.CREATE_REPAIR_PLAN),
            (admin, Permission.EXECUTE_REPAIR),
        )
        for context, permission in allowed:
            with self.subTest(permission=permission, allowed=True):
                self.assertIs(
                    require_api_permission(permission)(request, context),
                    context,
                )
        for context, permission in denied:
            with self.subTest(permission=permission, allowed=False):
                with self.assertRaises(AuthorizationError):
                    require_api_permission(permission)(request, context)


class EnterpriseApiAuthorizationTests(unittest.TestCase):
    def test_api_matrix_workflow_recheck_and_fixed_audit_actions(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(InMemorySaver(), audit, calls)
            current = {"value": _context("engineer-1", Role.ENGINEER)}
            app = create_enterprise_app(
                agent_workflow=graph,
                audit_log=audit,
                clock=lambda: NOW,
                user_context_provider=lambda request: current["value"],
            )

            with TestClient(app) as client:
                current["value"] = _context("admin-1", Role.ADMIN)
                denied_plan = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-ADMIN-DENIED",
                        "query": "repair link",
                    },
                )
                self.assertEqual(denied_plan.status_code, 403)

                current["value"] = _context("engineer-1", Role.ENGINEER)
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-RBAC-API",
                        "query": "repair link",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                decision = {
                    "decision": "approve",
                    "actor": "noc-admin",
                    "plan_digest": approval["plan_digest"],
                }

                denied_approval = client.post(
                    "/api/v1/incidents/INC-RBAC-API/approval",
                    json=decision,
                )
                self.assertEqual(denied_approval.status_code, 403)

                current["value"] = _context("admin-1", Role.ADMIN)
                approved = client.post(
                    "/api/v1/incidents/INC-RBAC-API/approval",
                    json=decision,
                )
                self.assertTrue(
                    any(name == "error" for name, _ in _events(approved.text))
                )
                status = client.get("/api/v1/incidents/INC-RBAC-API").json()
                self.assertEqual(status["enterprise_status"], "approved")
                self.assertEqual(calls, [])

                denied_execution = client.post(
                    "/api/v1/incidents/INC-RBAC-API/approval",
                    json=decision,
                )
                self.assertEqual(denied_execution.status_code, 403)

                current["value"] = _context("engineer-1", Role.ENGINEER)
                executed = client.post(
                    "/api/v1/incidents/INC-RBAC-API/approval",
                    json=decision,
                )
                answer = next(
                    data for name, data in _events(executed.text) if name == "answer"
                )

            self.assertEqual(answer["enterprise_status"], "executed")
            self.assertEqual(calls, ["ACTION-1"])
            authorization = [
                event
                for event in audit.list_all_events(
                    event_type=AuditEventType.DECISION
                )
                if event.action.startswith("authorize_api_")
            ]
            self.assertEqual(
                {event.action for event in authorization},
                {
                    "authorize_api_repair_plan",
                    "authorize_api_approval",
                    "authorize_api_execution",
                },
            )
            self.assertIn("allowed", {event.outcome for event in authorization})
            self.assertIn("denied", {event.outcome for event in authorization})
            create_events = [
                event
                for event in authorization
                if event.action == "authorize_api_repair_plan"
            ]
            self.assertEqual(
                {event.incident_id for event in create_events},
                {"INC-ADMIN-DENIED", "INC-RBAC-API"},
            )
            for event in authorization:
                serialized = repr(event.model_dump(mode="json"))
                self.assertNotIn("sensitive-", serialized)
                self.assertNotIn("query", serialized)
                self.assertNotIn("token", serialized.lower())
                self.assertIn("permission", event.details)
                self.assertIn("decision", event.details)
            public_payloads = started.text + approved.text + executed.text + repr(status)
            self.assertNotIn("UserContext", public_payloads)
            self.assertNotIn("rbac_contexts", public_payloads)
            self.assertNotIn("sensitive-", public_payloads)

    def test_configured_provider_cannot_return_missing_context(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, []),
                audit_log=audit,
                user_context_provider=lambda request: None,
            )

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/incidents",
                    json={"session_id": "session-1", "query": "repair link"},
                )

        self.assertEqual(response.status_code, 403)

    def test_unconfigured_authorization_preserves_legacy_api_behavior(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, calls),
                audit_log=audit,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                started = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-LEGACY-API",
                        "query": "repair link",
                    },
                )
                approval = next(
                    data
                    for name, data in _events(started.text)
                    if name == "approval_required"
                )
                resumed = client.post(
                    "/api/v1/incidents/INC-LEGACY-API/approval",
                    json={
                        "decision": "approve",
                        "actor": "legacy-operator",
                        "plan_digest": approval["plan_digest"],
                    },
                )

        self.assertTrue(any(name == "answer" for name, _ in _events(resumed.text)))
        self.assertEqual(calls, ["ACTION-1"])


if __name__ == "__main__":
    unittest.main()
