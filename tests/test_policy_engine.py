"""Deterministic Agent Policy Engine contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from network_agent_rag.packs.networkops.agents.enterprise import AllowlistedExecutor
from network_agent_rag.packs.networkops.api.enterprise import create_sqlite_enterprise_app
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.auth import (
    AuthorizationError,
    Permission,
    Role,
    User,
    UserContext,
)
from network_agent_rag.observability import SQLiteTraceStore
from network_agent_rag.policy import (
    PolicyCondition,
    PolicyContext,
    PolicyEffect,
    PolicyEngine,
    PolicyOperation,
    PolicyRegistry,
    PolicyRiskLevel,
    PolicyRule,
    aggregate_effect,
    default_policy_registry,
    evaluate_actions,
)
from tests.test_checkpoint import build_graph


NOW = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)


def _context(
    *,
    operation: PolicyOperation = PolicyOperation.VIEW_DEVICE_STATUS,
    risk_level: PolicyRiskLevel = PolicyRiskLevel.LOW,
    permission: Permission = Permission.EXECUTE_REPAIR,
    roles: tuple[Role, ...] = (Role.ENGINEER,),
) -> PolicyContext:
    return PolicyContext(
        actor_id="engineer-1",
        roles=roles,
        permission=permission,
        operation=operation,
        tool_name="network.view_device_status",
        devices=("SW1",),
        risk_level=risk_level,
        timestamp=NOW,
    )


def _rule(
    rule_id: str,
    effect: PolicyEffect,
    *,
    operation: PolicyOperation = PolicyOperation.VIEW_DEVICE_STATUS,
) -> PolicyRule:
    return PolicyRule(
        rule_id=rule_id,
        name=rule_id,
        condition=PolicyCondition(operation=frozenset({operation})),
        effect=effect,
    )


class PolicyModelTests(unittest.TestCase):
    def test_models_are_frozen_and_reject_extra_fields(self) -> None:
        context = _context()
        with self.assertRaises(ValidationError):
            PolicyContext(**context.model_dump(), unexpected=True)
        with self.assertRaises(ValidationError):
            context.actor_id = "changed"  # type: ignore[misc]

    def test_condition_requires_a_constraint_and_valid_device_range(self) -> None:
        with self.assertRaises(ValidationError):
            PolicyCondition()
        with self.assertRaises(ValidationError):
            PolicyCondition(min_devices=5, max_devices=2)

    def test_condition_uses_and_semantics_and_role_intersection(self) -> None:
        condition = PolicyCondition(
            operation=frozenset({PolicyOperation.VIEW_DEVICE_STATUS}),
            risk_level=frozenset({PolicyRiskLevel.LOW}),
            permission=frozenset({Permission.EXECUTE_REPAIR}),
            role=frozenset({Role.ENGINEER, Role.ADMIN}),
            min_devices=1,
            max_devices=2,
        )
        rule = PolicyRule(
            rule_id="allow-status",
            name="Allow status",
            condition=condition,
            effect=PolicyEffect.ALLOW,
        )
        engine = PolicyEngine(PolicyRegistry((rule,), {"status": PolicyOperation.VIEW_DEVICE_STATUS}))

        self.assertEqual(engine.evaluate(_context()).decision, PolicyEffect.ALLOW)
        self.assertEqual(
            engine.evaluate(_context(risk_level=PolicyRiskLevel.MEDIUM)).decision,
            PolicyEffect.DENY,
        )


class PolicyRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_rules_and_unknown_tool_is_explicit(self) -> None:
        rule = _rule("rule-1", PolicyEffect.ALLOW)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            PolicyRegistry((rule, rule), {})

        registry = PolicyRegistry((rule,), {"status": PolicyOperation.VIEW_DEVICE_STATUS})
        self.assertEqual(registry.resolve_operation("status"), PolicyOperation.VIEW_DEVICE_STATUS)
        self.assertEqual(registry.resolve_operation("unknown"), PolicyOperation.UNKNOWN)

    def test_registry_exposes_immutable_sorted_rules_and_mapping(self) -> None:
        registry = PolicyRegistry(
            (
                _rule("z-rule", PolicyEffect.ALLOW),
                _rule("a-rule", PolicyEffect.DENY),
            ),
            {"status": PolicyOperation.VIEW_DEVICE_STATUS},
        )

        self.assertEqual(tuple(rule.rule_id for rule in registry.rules), ("a-rule", "z-rule"))
        with self.assertRaises(TypeError):
            registry.tool_operations["other"] = PolicyOperation.RESTART_DEVICE  # type: ignore[index]
        with self.assertRaises((AttributeError, TypeError)):
            registry._rules = ()  # type: ignore[misc]


class PolicyEngineTests(unittest.TestCase):
    def test_default_rules_allow_deny_and_require_approval(self) -> None:
        engine = PolicyEngine(default_policy_registry())
        allowed = engine.evaluate(_context())
        approval = engine.evaluate(
            _context(
                operation=PolicyOperation.RESTART_DEVICE,
                risk_level=PolicyRiskLevel.HIGH,
            ).model_copy(update={"tool_name": "network.restart_device"})
        )
        denied = engine.evaluate(
            _context(
                operation=PolicyOperation.BATCH_CONFIGURATION_CHANGE,
                risk_level=PolicyRiskLevel.CRITICAL,
                permission=Permission.CREATE_REPAIR_PLAN,
            ).model_copy(update={"tool_name": "network.batch_configuration_change"})
        )

        self.assertEqual(allowed.decision, PolicyEffect.ALLOW)
        self.assertEqual(approval.decision, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(denied.decision, PolicyEffect.DENY)

    def test_decision_priority_is_deny_then_approval_then_allow(self) -> None:
        registry = PolicyRegistry(
            (
                _rule("allow", PolicyEffect.ALLOW),
                _rule("approval", PolicyEffect.REQUIRE_APPROVAL),
                _rule("deny", PolicyEffect.DENY),
            ),
            {"status": PolicyOperation.VIEW_DEVICE_STATUS},
        )
        decision = PolicyEngine(registry).evaluate(_context())

        self.assertEqual(decision.decision, PolicyEffect.DENY)
        self.assertEqual(decision.matched_rules, ("allow", "approval", "deny"))

    def test_unmatched_and_unknown_operations_fail_closed(self) -> None:
        engine = PolicyEngine(default_policy_registry())
        medium = engine.evaluate(_context(risk_level=PolicyRiskLevel.MEDIUM))
        unknown = engine.evaluate(
            _context(operation=PolicyOperation.UNKNOWN).model_copy(
                update={"tool_name": "vendor.unknown"}
            )
        )

        self.assertEqual(medium.decision, PolicyEffect.DENY)
        self.assertEqual(unknown.decision, PolicyEffect.DENY)
        self.assertEqual(unknown.matched_rules, ())

    def test_decision_id_is_deterministic_for_identical_input(self) -> None:
        engine = PolicyEngine(default_policy_registry())
        first = engine.evaluate(_context())
        second = engine.evaluate(_context())

        self.assertEqual(first, second)
        self.assertEqual(len(first.decision_id), 64)

    def test_action_evaluator_uses_explicit_mapping_and_aggregates(self) -> None:
        engine = PolicyEngine(default_policy_registry())
        user = UserContext(
            user=User(
                user_id="engineer-1",
                username="engineer",
                roles=(Role.ENGINEER,),
            )
        )
        actions = [
            {
                "action_id": "A-1",
                "tool_name": "network.view_device_status",
                "target": "SW1",
                "arguments": {},
                "verification_steps": ["verify"],
                "rollback_instructions": "none",
            },
            {
                "action_id": "A-2",
                "tool_name": "vendor.unknown",
                "target": "SW2",
                "arguments": {},
                "verification_steps": ["verify"],
                "rollback_instructions": "none",
            },
        ]

        evaluations = evaluate_actions(
            engine,
            user,
            actions,
            risk_level=PolicyRiskLevel.LOW,
            timestamp=NOW,
        )

        self.assertEqual(len(evaluations), 2)
        self.assertEqual(evaluations[0].context.devices, ("SW1", "SW2"))
        self.assertEqual(evaluations[1].context.operation, PolicyOperation.UNKNOWN)
        self.assertEqual(aggregate_effect(evaluations), PolicyEffect.DENY)


class PolicyWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engineer = UserContext(
            user=User(
                user_id="engineer-1",
                username="engineer",
                roles=(Role.ENGINEER,),
            )
        )
        self.admin = UserContext(
            user=User(
                user_id="admin-1",
                username="admin",
                roles=(Role.ADMIN,),
            )
        )

    @staticmethod
    def _action(tool_name: str) -> dict[str, object]:
        return {
            "action_id": "ACTION-1",
            "tool_name": tool_name,
            "target": "SW1",
            "arguments": {"credential": "must-not-be-audited"},
            "verification_steps": ["verify"],
            "rollback_instructions": "rollback",
        }

    @staticmethod
    def _repair_plan(risk_level: str) -> dict[str, object]:
        return {
            "risk_level": risk_level,
            "requires_human_approval": False,
            "steps": ["repair"],
            "verification_steps": ["verify"],
            "rollback_conditions": ["rollback"],
            "execution_status": "not_executed",
        }

    def _config(
        self,
        incident_id: str,
        execution: UserContext | None = None,
        approval: UserContext | None = None,
    ):
        contexts = {
            "repair_plan": self.engineer,
            "execution": execution or self.engineer,
        }
        if approval is not None:
            contexts["approval"] = approval
        return {
            "configurable": {
                "thread_id": incident_id,
                "rbac_contexts": contexts,
                "trace_id": f"trace-{incident_id}",
                "run_id": f"run-{incident_id}",
            }
        }

    def test_low_risk_policy_allows_execution_without_serializing_policy(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                policy_engine=PolicyEngine(default_policy_registry()),
                build_repair_plan=lambda state: self._repair_plan("low"),
                plan_actions=lambda state: [
                    self._action("network.view_device_status")
                ],
                action_executor=AllowlistedExecutor(
                    {
                        "network.view_device_status": lambda action: calls.append(
                            action["action_id"]
                        )
                        or {"status": "succeeded", "message": "ok"}
                    }
                ),
            )

            result = graph.invoke(
                {"user_query": "status", "incident_id": "INC-POLICY-ALLOW"},
                self._config("INC-POLICY-ALLOW"),
            )

            self.assertEqual(result["execution_result"]["status"], "succeeded")
            self.assertEqual(calls, ["ACTION-1"])
            serialized = repr(result)
            self.assertNotIn("PolicyContext", serialized)
            self.assertNotIn("PolicyDecision", serialized)

    def test_policy_can_force_existing_approval_for_low_risk_action(self) -> None:
        rule = PolicyRule(
            rule_id="approval-low-status",
            name="Approval low status",
            condition=PolicyCondition(
                operation=frozenset({PolicyOperation.VIEW_DEVICE_STATUS}),
                risk_level=frozenset({PolicyRiskLevel.LOW}),
            ),
            effect=PolicyEffect.REQUIRE_APPROVAL,
        )
        engine = PolicyEngine(
            PolicyRegistry(
                (rule,),
                {"network.view_device_status": PolicyOperation.VIEW_DEVICE_STATUS},
            )
        )
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                policy_engine=engine,
                build_repair_plan=lambda state: self._repair_plan("low"),
                plan_actions=lambda state: [
                    self._action("network.view_device_status")
                ],
                action_executor=AllowlistedExecutor(
                    {
                        "network.view_device_status": lambda action: calls.append(
                            action["action_id"]
                        )
                        or {"status": "succeeded", "message": "ok"}
                    }
                ),
            )

            result = graph.invoke(
                {"user_query": "status", "incident_id": "INC-POLICY-APPROVAL"},
                self._config("INC-POLICY-APPROVAL"),
            )

            self.assertIn("__interrupt__", result)
            snapshot = graph.get_state(self._config("INC-POLICY-APPROVAL"))
            self.assertTrue(snapshot.values["risk_decision"]["approval_required"])
            self.assertIn(
                "policy requires approval",
                snapshot.values["risk_decision"]["reasons"],
            )
            final = graph.invoke(
                Command(
                    resume={
                        "decision": "approve",
                        "actor": "noc-admin",
                        "plan_digest": snapshot.values["risk_decision"][
                            "plan_digest"
                        ],
                    }
                ),
                self._config(
                    "INC-POLICY-APPROVAL",
                    execution=self.engineer,
                    approval=self.admin,
                ),
            )

            self.assertEqual(final["execution_result"]["status"], "succeeded")
            self.assertEqual(calls, ["ACTION-1"])

    def test_deny_blocks_before_execution_trace_tool_audit_and_executor(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            calls: list[str] = []
            graph = build_graph(
                InMemorySaver(),
                audit,
                calls,
                trace_store=trace,
                policy_engine=PolicyEngine(default_policy_registry()),
                build_repair_plan=lambda state: self._repair_plan("medium"),
                plan_actions=lambda state: [
                    self._action("network.view_device_status")
                ],
                action_executor=AllowlistedExecutor(
                    {
                        "network.view_device_status": lambda action: calls.append(
                            action["action_id"]
                        )
                        or {"status": "succeeded", "message": "unexpected"}
                    }
                ),
            )

            result = graph.invoke(
                {"user_query": "status", "incident_id": "INC-POLICY-DENY"},
                self._config("INC-POLICY-DENY"),
            )

            self.assertEqual(result["execution_result"]["status"], "blocked")
            self.assertEqual(
                result["execution_result"]["error_code"], "POLICY_DENIED"
            )
            self.assertEqual(calls, [])
            self.assertNotIn("Execute", {span.name for span in trace.list_all_spans()})
            self.assertFalse(
                any(
                    event.event_type == AuditEventType.TOOL_CALL
                    and event.actor == "Execute"
                    for event in audit.list_all_events()
                )
            )
            policy_events = [
                event
                for event in audit.list_all_events()
                if event.action.startswith("policy_")
            ]
            self.assertEqual(
                {event.action for event in policy_events},
                {"policy_evaluation", "policy_denied"},
            )
            self.assertEqual(len(policy_events), 2)
            self.assertNotIn("must-not-be-audited", repr(policy_events))

    def test_rbac_failure_prevents_policy_evaluation(self) -> None:
        class CountingEngine(PolicyEngine):
            calls = 0

            def evaluate(self, context: PolicyContext):
                self.calls += 1
                return super().evaluate(context)

        engine = CountingEngine(default_policy_registry())
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            graph = build_graph(
                InMemorySaver(),
                audit,
                [],
                policy_engine=engine,
                build_repair_plan=lambda state: self._repair_plan("low"),
                plan_actions=lambda state: [
                    self._action("network.view_device_status")
                ],
            )

            with self.assertRaises(AuthorizationError):
                graph.invoke(
                    {"user_query": "status", "incident_id": "INC-POLICY-RBAC"},
                    self._config("INC-POLICY-RBAC", self.admin),
                )

            self.assertEqual(engine.calls, 0)

    def test_policy_aware_factory_is_explicit_and_receives_engine(self) -> None:
        received: list[PolicyEngine] = []

        def factory(checkpointer, *, policy_engine, **dependencies):
            received.append(policy_engine)
            return object()

        with TemporaryDirectory() as directory:
            app = create_sqlite_enterprise_app(
                policy_workflow_factory=factory,
                checkpoint_path=Path(directory) / "checkpoints.sqlite3",
                audit_path=Path(directory) / "audit.sqlite3",
                observability_path=Path(directory) / "trace.sqlite3",
                benchmark_results_path=Path(directory) / "benchmarks",
            )
            from fastapi.testclient import TestClient

            with TestClient(app):
                pass

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], PolicyEngine)

    def test_policy_aware_factory_must_accept_policy_engine(self) -> None:
        def incompatible_factory(checkpointer, *, audit_log=None):
            return object()

        with TemporaryDirectory() as directory:
            app = create_sqlite_enterprise_app(
                policy_workflow_factory=incompatible_factory,
                checkpoint_path=Path(directory) / "checkpoints.sqlite3",
                audit_path=Path(directory) / "audit.sqlite3",
                observability_path=Path(directory) / "trace.sqlite3",
                benchmark_results_path=Path(directory) / "benchmarks",
            )
            from fastapi.testclient import TestClient

            with self.assertRaises(TypeError), TestClient(app):
                pass


if __name__ == "__main__":
    unittest.main()
