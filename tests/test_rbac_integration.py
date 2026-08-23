"""Runtime RBAC integration tests for the enterprise workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from network_agent_rag.audit import AuditEvent, AuditEventType, SQLiteAuditLog
from network_agent_rag.packs.networkops.api.enterprise import create_enterprise_app
from network_agent_rag.auth import (
    AuthorizationError,
    Permission,
    Role,
    User,
    UserContext,
    authorizing_role,
    require_permission,
)
from network_agent_rag.observability import TraceCollector, TraceEventType
from tests.test_checkpoint import build_graph


class UserContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engineer = User(
            user_id=" engineer-1 ",
            username="sensitive-engineer-name",
            roles=(Role.ENGINEER,),
        )

    def test_context_api_is_exported(self) -> None:
        self.assertIsNotNone(UserContext)
        self.assertIsNotNone(AuthorizationError)
        self.assertIsNotNone(require_permission)
        self.assertIsNotNone(authorizing_role)

    def test_context_is_frozen_and_rejects_extra_fields(self) -> None:
        context = UserContext(user=self.engineer)

        with self.assertRaises(ValidationError):
            context.user = User(
                user_id="other", username="other", roles=(Role.ADMIN,)
            )
        with self.assertRaises(ValidationError):
            UserContext.model_validate({"user": self.engineer, "token": "secret"})

    def test_require_permission_and_authorizing_role_enforce_separation(self) -> None:
        context = UserContext(user=self.engineer)

        self.assertIsNone(
            require_permission(context, Permission.CREATE_REPAIR_PLAN)
        )
        self.assertEqual(
            authorizing_role(context, Permission.CREATE_REPAIR_PLAN),
            Role.ENGINEER,
        )
        self.assertIsNone(authorizing_role(context, Permission.APPROVE_REPAIR))

        with self.assertRaises(AuthorizationError) as captured:
            require_permission(context, Permission.APPROVE_REPAIR)

        error = captured.exception
        self.assertEqual(error.user_id, "engineer-1")
        self.assertIs(error.required_permission, Permission.APPROVE_REPAIR)
        message = str(error)
        self.assertNotIn(self.engineer.username, message)
        self.assertNotIn("Engineer", message)
        self.assertNotIn("roles", message.lower())
        self.assertNotIn("traceback", message.lower())


class AuditActorProjectionTests(unittest.TestCase):
    def test_actor_projection_is_optional_and_reads_existing_details(self) -> None:
        common = {
            "event_id": "event-1",
            "incident_id": "INC-1",
            "event_type": AuditEventType.DECISION,
            "actor": "authorization",
            "action": "authorize_repair_plan",
            "outcome": "allowed",
            "created_at": datetime(2026, 7, 16, tzinfo=timezone.utc),
        }

        current = AuditEvent(
            **common,
            details={"actor_id": "engineer-1", "actor_role": "Engineer"},
        )
        legacy = AuditEvent(**{**common, "event_id": "event-2"}, details={})

        self.assertEqual(current.actor_id, "engineer-1")
        self.assertEqual(current.actor_role, "Engineer")
        self.assertIsNone(legacy.actor_id)
        self.assertIsNone(legacy.actor_role)

    def test_sqlite_round_trip_projects_actor_without_schema_changes(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            recorded = audit.record(
                incident_id="INC-1",
                event_type=AuditEventType.DECISION,
                actor="authorization",
                action="authorize_execution",
                outcome="allowed",
                details={"actor_id": "engineer-1", "actor_role": "Engineer"},
            )
            loaded = audit.list_events("INC-1")

        self.assertEqual(recorded.actor_id, "engineer-1")
        self.assertEqual(loaded[0].actor_id, "engineer-1")
        self.assertEqual(loaded[0].actor_role, "Engineer")


def _context(user_id: str, role: Role) -> UserContext:
    return UserContext(
        user=User(
            user_id=user_id,
            username=f"sensitive-{user_id}",
            roles=(role,),
        )
    )


def _low_risk_plan(state):
    return {
        "risk_level": "low",
        "requires_human_approval": False,
        "steps": ["apply approved change"],
        "verification_steps": ["verify service"],
        "rollback_conditions": ["service verification fails"],
        "execution_status": "not_executed",
    }


def _config(incident_id: str, contexts: object | None = None):
    configurable: dict[str, object] = {"thread_id": incident_id}
    if contexts is not None:
        configurable["rbac_contexts"] = contexts
    return {"configurable": configurable}


class EnterpriseRBACWorkflowTests(unittest.TestCase):
    def _resources(self):
        temporary = TemporaryDirectory()
        audit = SQLiteAuditLog(Path(temporary.name) / "audit.sqlite3")
        saver = InMemorySaver()
        calls: list[str] = []
        return temporary, audit, saver, calls

    def test_legacy_workflow_runs_without_rbac_contexts(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        graph = build_graph(
            saver,
            audit,
            calls,
            build_repair_plan=_low_risk_plan,
        )

        final = graph.invoke(
            {"user_query": "check link", "incident_id": "INC-LEGACY"},
            _config("INC-LEGACY"),
        )

        self.assertEqual(final["enterprise_status"], "executed")
        self.assertEqual(calls, ["ACTION-1"])
        self.assertFalse(
            any(event.action.startswith("authorize_") for event in audit.list_events("INC-LEGACY"))
        )

    def test_explicit_contexts_enforce_repair_and_execution_permissions(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        graph = build_graph(
            saver,
            audit,
            calls,
            build_repair_plan=_low_risk_plan,
        )
        engineer = _context("engineer-1", Role.ENGINEER)

        final = graph.invoke(
            {"user_query": "check link", "incident_id": "INC-ALLOWED"},
            _config(
                "INC-ALLOWED",
                MappingProxyType(
                    {"repair_plan": engineer, "execution": engineer}
                ),
            ),
        )

        self.assertEqual(final["enterprise_status"], "executed")
        self.assertEqual(calls, ["ACTION-1"])
        authorization = [
            event
            for event in audit.list_events(
                "INC-ALLOWED", event_type=AuditEventType.DECISION
            )
            if event.action.startswith("authorize_")
        ]
        self.assertEqual(
            [event.action for event in authorization],
            ["authorize_repair_plan", "authorize_execution"],
        )
        self.assertTrue(all(event.outcome == "allowed" for event in authorization))
        self.assertTrue(all(event.actor_id == "engineer-1" for event in authorization))
        self.assertTrue(all(event.actor_role == "Engineer" for event in authorization))
        self.assertEqual(
            [event.details["permission"] for event in authorization],
            [
                Permission.CREATE_REPAIR_PLAN.value,
                Permission.EXECUTE_REPAIR.value,
            ],
        )
        self.assertTrue(
            all("required_permission" not in event.details for event in authorization)
        )

    def test_admin_cannot_create_repair_plan(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        build_calls: list[str] = []

        def build_plan(state):
            build_calls.append("called")
            return _low_risk_plan(state)

        graph = build_graph(
            saver,
            audit,
            calls,
            build_repair_plan=build_plan,
        )

        with self.assertRaises(AuthorizationError) as captured:
            graph.invoke(
                {"user_query": "check link", "incident_id": "INC-ADMIN-PLAN"},
                _config(
                    "INC-ADMIN-PLAN",
                    {"repair_plan": _context("admin-1", Role.ADMIN)},
                ),
            )

        self.assertIs(
            captured.exception.required_permission,
            Permission.CREATE_REPAIR_PLAN,
        )
        self.assertEqual(build_calls, [])
        self.assertEqual(calls, [])

    def test_high_risk_flow_uses_separate_engineer_admin_engineer_contexts(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        graph = build_graph(saver, audit, calls)
        engineer = _context("engineer-1", Role.ENGINEER)
        admin = _context("admin-1", Role.ADMIN)
        initial_config = _config(
            "INC-SEPARATION",
            {"repair_plan": engineer},
        )

        first = graph.invoke(
            {"user_query": "repair link", "incident_id": "INC-SEPARATION"},
            initial_config,
        )
        snapshot = graph.get_state(initial_config)
        digest = snapshot.values["risk_decision"]["plan_digest"]

        self.assertIn("__interrupt__", first)
        final = graph.invoke(
            Command(
                resume={
                    "decision": "approve",
                    "actor": "noc-admin",
                    "plan_digest": digest,
                }
            ),
            _config(
                "INC-SEPARATION",
                {"approval": admin, "execution": engineer},
            ),
        )

        self.assertEqual(final["enterprise_status"], "executed")
        self.assertEqual(calls, ["ACTION-1"])
        actions = {
            event.action: event
            for event in audit.list_events("INC-SEPARATION")
            if event.action.startswith("authorize_")
        }
        self.assertEqual(
            set(actions),
            {
                "authorize_repair_plan",
                "authorize_approval",
                "authorize_execution",
            },
        )
        self.assertEqual(actions["authorize_approval"].actor_id, "admin-1")
        self.assertEqual(actions["authorize_approval"].actor_role, "Admin")

        persisted = repr(saver.storage)
        self.assertNotIn("UserContext", persisted)
        self.assertNotIn("sensitive-engineer-1", persisted)
        self.assertNotIn("sensitive-admin-1", persisted)
        self.assertNotIn("rbac_contexts", persisted)
        self.assertNotIn("rbac_contexts", snapshot.values)
        self.assertNotIn("rbac_contexts", final)

    def test_explicit_mode_rejects_missing_or_malformed_contexts(self) -> None:
        cases = (
            ({}, AuthorizationError),
            ([], TypeError),
            ({"repair_plan": object()}, TypeError),
            ({1: _context("engineer-1", Role.ENGINEER)}, TypeError),
        )

        for index, (contexts, error_type) in enumerate(cases):
            with self.subTest(contexts=contexts):
                temporary, audit, saver, calls = self._resources()
                self.addCleanup(temporary.cleanup)
                graph = build_graph(
                    saver,
                    audit,
                    calls,
                    build_repair_plan=_low_risk_plan,
                )
                with self.assertRaises(error_type):
                    graph.invoke(
                        {
                            "user_query": "check link",
                            "incident_id": f"INC-INVALID-{index}",
                        },
                        _config(f"INC-INVALID-{index}", contexts),
                    )
                self.assertEqual(calls, [])

    def test_missing_approval_context_fails_only_after_resume(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        graph = build_graph(saver, audit, calls)
        engineer = _context("engineer-1", Role.ENGINEER)
        initial_config = _config(
            "INC-MISSING-APPROVAL", {"repair_plan": engineer}
        )
        first = graph.invoke(
            {
                "user_query": "repair link",
                "incident_id": "INC-MISSING-APPROVAL",
            },
            initial_config,
        )
        digest = graph.get_state(initial_config).values["risk_decision"][
            "plan_digest"
        ]

        self.assertIn("__interrupt__", first)
        with self.assertRaises(AuthorizationError) as captured:
            graph.invoke(
                Command(
                    resume={
                        "decision": "approve",
                        "actor": "noc-admin",
                        "plan_digest": digest,
                    }
                ),
                _config(
                    "INC-MISSING-APPROVAL",
                    {"execution": engineer},
                ),
            )

        self.assertIs(
            captured.exception.required_permission,
            Permission.APPROVE_REPAIR,
        )
        self.assertEqual(calls, [])

    def test_resume_cannot_drop_rbac_contexts_to_reenter_legacy_mode(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        engineer = _context("engineer-1", Role.ENGINEER)
        initial_config = _config(
            "INC-NO-DOWNGRADE", {"repair_plan": engineer}
        )
        graph = build_graph(saver, audit, calls)
        graph.invoke(
            {
                "user_query": "repair link",
                "incident_id": "INC-NO-DOWNGRADE",
            },
            initial_config,
        )
        digest = graph.get_state(initial_config).values["risk_decision"][
            "plan_digest"
        ]

        reopened_graph = build_graph(saver, audit, calls)
        with self.assertRaises(AuthorizationError) as captured:
            reopened_graph.invoke(
                Command(
                    resume={
                        "decision": "approve",
                        "actor": "untrusted",
                        "plan_digest": digest,
                    }
                ),
                _config("INC-NO-DOWNGRADE"),
            )

        self.assertIs(
            captured.exception.required_permission,
            Permission.APPROVE_REPAIR,
        )
        self.assertEqual(calls, [])

    def test_explicit_rbac_requires_persistent_audit_marker(self) -> None:
        saver = InMemorySaver()
        calls: list[str] = []
        graph = build_graph(
            saver,
            None,
            calls,
            build_repair_plan=_low_risk_plan,
        )

        with self.assertRaisesRegex(RuntimeError, "audit_log"):
            graph.invoke(
                {"user_query": "check link", "incident_id": "INC-NO-AUDIT"},
                _config(
                    "INC-NO-AUDIT",
                    {"repair_plan": _context("engineer-1", Role.ENGINEER)},
                ),
            )
        self.assertEqual(calls, [])

    def test_engineer_cannot_approve_high_risk_plan(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        graph = build_graph(saver, audit, calls)
        engineer = _context("engineer-1", Role.ENGINEER)
        initial_config = _config(
            "INC-DENY-APPROVAL", {"repair_plan": engineer}
        )
        graph.invoke(
            {
                "user_query": "repair link",
                "incident_id": "INC-DENY-APPROVAL",
            },
            initial_config,
        )
        digest = graph.get_state(initial_config).values["risk_decision"][
            "plan_digest"
        ]

        with self.assertRaises(AuthorizationError) as captured:
            graph.invoke(
                Command(
                    resume={
                        "decision": "approve",
                        "actor": "engineer-1",
                        "plan_digest": digest,
                    }
                ),
                _config(
                    "INC-DENY-APPROVAL",
                    {"approval": engineer, "execution": engineer},
                ),
            )

        self.assertIs(
            captured.exception.required_permission,
            Permission.APPROVE_REPAIR,
        )
        self.assertEqual(calls, [])
        event = next(
            item
            for item in audit.list_events("INC-DENY-APPROVAL")
            if item.action == "authorize_approval"
        )
        self.assertEqual(event.outcome, "denied")
        self.assertEqual(event.actor_id, "engineer-1")
        self.assertEqual(event.actor_role, "Engineer")
        self.assertEqual(
            event.details["permission"],
            Permission.APPROVE_REPAIR.value,
        )

    def test_unauthorized_execution_has_no_executor_or_trace_side_effect(self) -> None:
        temporary, audit, saver, calls = self._resources()
        self.addCleanup(temporary.cleanup)
        collector = TraceCollector(audit)
        graph = build_graph(
            saver,
            audit,
            calls,
            trace_collector=collector,
            build_repair_plan=_low_risk_plan,
        )
        engineer = _context("engineer-1", Role.ENGINEER)
        admin = _context("admin-1", Role.ADMIN)
        config = _config(
            "INC-DENY-EXECUTION",
            {"repair_plan": engineer, "execution": admin},
        )
        config["configurable"].update(
            {"trace_id": "trace-denied", "run_id": "run-denied"}
        )

        with self.assertRaises(AuthorizationError) as captured:
            graph.invoke(
                {
                    "user_query": "check link",
                    "incident_id": "INC-DENY-EXECUTION",
                },
                config,
            )

        self.assertIs(
            captured.exception.required_permission,
            Permission.EXECUTE_REPAIR,
        )
        self.assertEqual(calls, [])
        repair_events = [
            event
            for event in audit.list_events(
                "INC-DENY-EXECUTION", event_type=AuditEventType.TRACE
            )
            if event.action == TraceEventType.REPAIR_EXECUTE.value
        ]
        tool_events = [
            event
            for event in audit.list_events(
                "INC-DENY-EXECUTION", event_type=AuditEventType.TOOL_CALL
            )
            if event.actor == "Execute"
        ]
        self.assertEqual(repair_events, [])
        self.assertEqual(tool_events, [])

    def test_rbac_context_is_not_part_of_enterprise_api_or_sse_schema(self) -> None:
        serialized_schema = repr(create_enterprise_app().openapi())

        self.assertNotIn("UserContext", serialized_schema)
        self.assertNotIn("rbac_contexts", serialized_schema)


if __name__ == "__main__":
    unittest.main()
