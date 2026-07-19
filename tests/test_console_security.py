"""Authentication and read-only isolation tests for the Operator Console."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from fastapi.testclient import TestClient

from network_agent_rag.api.enterprise import create_enterprise_app
from network_agent_rag.auth import (
    AuthorizationError,
    JWTProvider,
    JWTTokenManager,
    Permission,
    Role,
    User,
    UserContext,
)
from network_agent_rag.auth.dependencies import current_user_dependency
from network_agent_rag.governance import GovernanceService
from tests.test_console_api import NOW, SECRET, _headers, build_console_fixture


FORBIDDEN = (
    "token",
    "secret",
    "password",
    "prompt",
    "credential",
    "hash",
    "must-not-escape",
)


class ConsoleSecurityTests(unittest.TestCase):
    def test_console_requires_authentication_provider_and_bearer_token(self) -> None:
        anonymous = create_enterprise_app()
        configured = create_enterprise_app(
            authentication_provider=JWTProvider(
                JWTTokenManager(SECRET, clock=lambda: NOW)
            )
        )
        with TestClient(anonymous) as client:
            no_provider = client.get("/api/v1/console/evaluations")
        with TestClient(configured) as client:
            no_header = client.get("/api/v1/console/evaluations")
            invalid = client.get(
                "/api/v1/console/evaluations",
                headers={"Authorization": "Bearer invalid"},
            )

        for response in (no_provider, no_header, invalid):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Unauthorized"})
            self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_authenticated_user_without_permission_receives_403(self) -> None:
        manager = JWTTokenManager(SECRET, clock=lambda: NOW)
        app = create_enterprise_app(authentication_provider=JWTProvider(manager))
        user = User(
            user_id="no-permission",
            username="no-permission",
            roles=(Role.OPERATOR,),
        )
        app.dependency_overrides[current_user_dependency] = lambda: UserContext.model_construct(
            user=user
        )
        with (
            patch(
                "network_agent_rag.auth.dependencies.require_context_permission",
                side_effect=AuthorizationError(
                    "no-permission", Permission.VIEW_TRACE
                ),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/api/v1/console/evaluations")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Forbidden"})

    def test_gets_only_append_auth_audit_and_never_mutate_projection_sources(self) -> None:
        with TemporaryDirectory() as directory:
            app, headers, workflow, audit, trace = build_console_fixture(directory)
            before_state = deepcopy(workflow.states)
            before_spans = trace.list_all_spans()
            before_audit = audit.list_all_events()
            before_benchmarks = app.state.benchmark_store.list_runs()
            calls = {"query": 0, "sync": 0}
            original_query = GovernanceService.query_events

            def query(service, *args, **kwargs):
                calls["query"] += 1
                return original_query(service, *args, **kwargs)

            def sync(*args, **kwargs):
                calls["sync"] += 1
                raise AssertionError("Console must not synchronize governance events")

            with (
                patch.object(GovernanceService, "query_events", new=query),
                patch.object(GovernanceService, "sync_security_events", new=sync),
                TestClient(app) as client,
            ):
                responses = [
                    client.get(path, headers=headers)
                    for path in (
                        "/api/v1/console/dashboard",
                        "/api/v1/console/incidents",
                        "/api/v1/console/incidents/INC-1",
                        "/api/v1/console/incidents/INC-1/trace",
                        "/api/v1/console/security-events",
                        "/api/v1/console/policy-decisions",
                        "/api/v1/console/evaluations",
                    )
                ]

            self.assertTrue(all(response.status_code == 200 for response in responses))
            self.assertGreater(calls["query"], 0)
            self.assertEqual(calls["sync"], 0)
            self.assertEqual(workflow.states, before_state)
            self.assertEqual(trace.list_all_spans(), before_spans)
            self.assertEqual(app.state.benchmark_store.list_runs(), before_benchmarks)
            added = audit.list_all_events()[len(before_audit) :]
            self.assertTrue(added)
            self.assertTrue(
                all(
                    event.action in {
                        "authenticate_success",
                        "authorize_api_view_incident",
                        "authorize_api_view_trace",
                    }
                    for event in added
                )
            )
            serialized = json.dumps(
                [response.json() for response in responses], ensure_ascii=False
            ).lower()
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, serialized)

    def test_console_module_has_no_mutating_runtime_dependencies(self) -> None:
        source = Path(
            "src/network_agent_rag/api/console.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "PolicyEngine",
            "AgentEvaluationRunner",
            "sync_security_events",
            "update_state",
            "aupdate_state",
            "executor",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
