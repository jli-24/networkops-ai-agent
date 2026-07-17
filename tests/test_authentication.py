"""JWT authentication layer tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

import jwt
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from starlette.requests import Request

import network_agent_rag.auth as auth
from network_agent_rag.api.enterprise import (
    create_enterprise_app,
    create_sqlite_enterprise_app,
)
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.auth import (
    AuthenticationError,
    JWTProvider,
    JWTTokenManager,
    Role,
    UserContext,
    UserIdentity,
)
from network_agent_rag.auth.dependencies import (
    current_user_dependency,
    get_current_user_context,
)
from tests.test_checkpoint import build_graph


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SECRET = "a" * 32


def _identity(role: Role = Role.ENGINEER) -> UserIdentity:
    return UserIdentity(
        user_id=" user-1 ",
        username=" noc-user ",
        email=" noc@example.test ",
        roles=(role,),
    )


def _manager(current: dict[str, datetime] | None = None) -> JWTTokenManager:
    clock = current or {"now": NOW}
    return JWTTokenManager(SECRET, clock=lambda: clock["now"])


def _sse_events(payload: str) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    event = ""
    for line in payload.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            result.append((event, json.loads(line[6:])))
    return result


class AuthenticationPublicApiTests(unittest.TestCase):
    def test_authentication_api_is_exported(self) -> None:
        for name in (
            "AuthenticationError",
            "AuthenticationProvider",
            "JWTProvider",
            "JWTTokenManager",
            "UserIdentity",
            "current_user_dependency",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(auth, name), name)

        self.assertIsNotNone(current_user_dependency)
        self.assertIsNotNone(get_current_user_context)


class UserIdentityTests(unittest.TestCase):
    def test_identity_is_strict_frozen_and_compatible_with_user(self) -> None:
        identity = _identity()

        self.assertEqual(identity.user_id, "user-1")
        self.assertEqual(identity.username, "noc-user")
        self.assertEqual(identity.email, "noc@example.test")
        self.assertEqual(identity.to_user().roles, (Role.ENGINEER,))
        with self.assertRaises(ValidationError):
            identity.username = "changed"

    def test_identity_rejects_empty_duplicate_invalid_and_extra_values(self) -> None:
        cases = (
            {"user_id": " ", "username": "u", "roles": (Role.OPERATOR,)},
            {"user_id": "u", "username": " ", "roles": (Role.OPERATOR,)},
            {"user_id": "u", "username": "u", "roles": ()},
            {
                "user_id": "u",
                "username": "u",
                "roles": (Role.OPERATOR, Role.OPERATOR),
            },
            {"user_id": "u", "username": "u", "roles": ("engineer",)},
            {
                "user_id": "u",
                "username": "u",
                "roles": (Role.OPERATOR,),
                "password": "forbidden",
            },
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                UserIdentity.model_validate(payload)


class JWTTokenManagerTests(unittest.TestCase):
    def test_round_trip_uses_fixed_safe_identity_claims(self) -> None:
        manager = _manager()
        identity = _identity()

        token = manager.create_token(identity)
        payload = jwt.decode(token, options={"verify_signature": False})

        self.assertEqual(manager.verify_token(token), identity)
        self.assertEqual(payload["sub"], "user-1")
        self.assertEqual(payload["username"], "noc-user")
        self.assertEqual(payload["email"], "noc@example.test")
        self.assertEqual(payload["roles"], ["Engineer"])
        self.assertEqual(set(payload), {"sub", "username", "email", "roles", "iat", "exp"})
        serialized = repr(payload).lower()
        for forbidden in ("password", "secret", "authorization", "api_key"):
            self.assertNotIn(forbidden, serialized)

    def test_rejects_expired_tampered_wrong_signature_and_invalid_claims(self) -> None:
        current = {"now": NOW}
        manager = _manager(current)
        token = manager.create_token(_identity())
        current["now"] = NOW + timedelta(minutes=31)
        with self.assertRaises(AuthenticationError):
            manager.verify_token(token)

        current["now"] = NOW
        with self.assertRaises(AuthenticationError):
            JWTTokenManager("b" * 32, clock=lambda: NOW).verify_token(token)

        header, payload, signature = token.split(".")
        replacement = "A" if payload[0] != "A" else "B"
        with self.assertRaises(AuthenticationError):
            manager.verify_token(".".join((header, replacement + payload[1:], signature)))

        invalid_role = jwt.encode(
            {
                "sub": "user-1",
                "username": "noc-user",
                "roles": ["engineer"],
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(minutes=30)).timestamp()),
            },
            SECRET,
            algorithm="HS256",
        )
        missing_subject = jwt.encode(
            {
                "username": "noc-user",
                "roles": ["Engineer"],
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(minutes=30)).timestamp()),
            },
            SECRET,
            algorithm="HS256",
        )
        future_issued = jwt.encode(
            {
                "sub": "user-1",
                "username": "noc-user",
                "roles": ["Engineer"],
                "iat": int((NOW + timedelta(minutes=1)).timestamp()),
                "exp": int((NOW + timedelta(minutes=30)).timestamp()),
            },
            SECRET,
            algorithm="HS256",
        )
        for invalid in (
            invalid_role,
            missing_subject,
            future_issued,
            "not-a-token",
        ):
            with self.subTest(token=invalid[:12]), self.assertRaises(AuthenticationError):
                manager.verify_token(invalid)

    def test_rejects_weak_configuration(self) -> None:
        for arguments in (
            {"secret_key": "short"},
            {"secret_key": SECRET, "algorithm": "none"},
            {"secret_key": SECRET, "expire_minutes": 0},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                JWTTokenManager(**arguments)


class JWTProviderTests(unittest.TestCase):
    def test_authenticates_bearer_header_and_rejects_invalid_transport(self) -> None:
        manager = _manager()
        provider = JWTProvider(manager)
        token = manager.create_token(_identity())
        app = create_enterprise_app(authentication_provider=provider)

        valid = Request(
            {
                "type": "http",
                "app": app,
                "path_params": {},
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            }
        )
        self.assertEqual(provider.authenticate(valid), _identity())

        headers = ([], [(b"authorization", b"Basic abc")], [(b"authorization", b"Bearer")])
        for raw_headers in headers:
            request = Request(
                {
                    "type": "http",
                    "app": app,
                    "path_params": {},
                    "headers": raw_headers,
                }
            )
            with self.subTest(headers=raw_headers), self.assertRaises(AuthenticationError):
                provider.authenticate(request)


class AuthenticationApiTests(unittest.TestCase):
    def test_api_rejects_malformed_and_expired_bearer_tokens(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            current = {"now": NOW}
            manager = _manager(current)
            expired_token = manager.create_token(_identity())
            current["now"] = NOW + timedelta(minutes=31)
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, []),
                audit_log=audit,
                authentication_provider=JWTProvider(manager),
            )
            with TestClient(app) as client:
                responses = (
                    client.post(
                        "/api/v1/incidents",
                        headers={"Authorization": "Basic credentials"},
                        json={"session_id": "session-1", "query": "repair link"},
                    ),
                    client.post(
                        "/api/v1/incidents",
                        headers={"Authorization": "Bearer"},
                        json={"session_id": "session-1", "query": "repair link"},
                    ),
                    client.post(
                        "/api/v1/incidents",
                        headers={"Authorization": f"Bearer {expired_token}"},
                        json={"session_id": "session-1", "query": "repair link"},
                    ),
                )

        for response in responses:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "Unauthorized"})
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_api_separates_authentication_401_from_authorization_403(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            manager = _manager()
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, []),
                audit_log=audit,
                clock=lambda: NOW,
                authentication_provider=JWTProvider(manager),
            )
            with TestClient(app) as client:
                missing = client.post(
                    "/api/v1/incidents",
                    json={"session_id": "session-1", "query": "repair link"},
                )
                invalid = client.post(
                    "/api/v1/incidents",
                    headers={"Authorization": "Bearer invalid"},
                    json={"session_id": "session-1", "query": "repair link"},
                )
                admin = client.post(
                    "/api/v1/incidents",
                    headers={
                        "Authorization": f"Bearer {manager.create_token(_identity(Role.ADMIN))}"
                    },
                    json={"session_id": "session-1", "query": "repair link"},
                )
                engineer = client.post(
                    "/api/v1/incidents",
                    headers={
                        "Authorization": f"Bearer {manager.create_token(_identity())}"
                    },
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-JWT",
                        "query": "repair link",
                    },
                )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json(), {"detail": "Unauthorized"})
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(admin.status_code, 403)
        self.assertEqual(engineer.status_code, 200)
        self.assertTrue(any(name == "approval_required" for name, _ in _sse_events(engineer.text)))

    def test_authentication_audit_is_fixed_and_never_records_credentials(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            manager = _manager()
            token = manager.create_token(_identity())
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, []),
                audit_log=audit,
                clock=lambda: NOW,
                authentication_provider=JWTProvider(manager),
            )
            with TestClient(app) as client:
                client.post(
                    "/api/v1/incidents",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-AUTH-AUDIT",
                        "query": "repair link",
                    },
                )
                client.post(
                    "/api/v1/incidents",
                    headers={"Authorization": "Bearer invalid"},
                    json={"session_id": "session-1", "query": "repair link"},
                )

            events = [
                event
                for event in audit.list_all_events(event_type=AuditEventType.DECISION)
                if event.action.startswith("authenticate_")
            ]

        self.assertEqual(
            [event.action for event in events],
            ["authenticate_success", "authenticate_failed"],
        )
        self.assertEqual(events[0].details["actor_id"], "user-1")
        self.assertEqual(events[1].details["actor_id"], "anonymous")
        self.assertEqual({event.details["source"] for event in events}, {"api"})
        serialized = repr([event.model_dump(mode="json") for event in events]).lower()
        self.assertNotIn(token.lower(), serialized)
        for forbidden in ("authorization", SECRET, "noc-user", "noc@example.test"):
            self.assertNotIn(forbidden.lower(), serialized)

    def test_legacy_provider_remains_supported_but_providers_are_mutually_exclusive(self) -> None:
        context = UserContext(user=_identity().to_user())
        app = create_enterprise_app(user_context_provider=lambda request: context)
        request = Request({"type": "http", "app": app, "path_params": {}, "headers": []})

        self.assertIs(current_user_dependency(request), context)
        self.assertIs(get_current_user_context(request), context)
        with self.assertRaises(ValueError):
            create_enterprise_app(
                authentication_provider=JWTProvider(_manager()),
                user_context_provider=lambda request: context,
            )

    def test_sqlite_factory_uses_jwt_environment_only_when_configured(self) -> None:
        factory = lambda checkpointer, audit_log: object()
        with patch.dict(
            os.environ,
            {
                "JWT_SECRET_KEY": SECRET,
                "JWT_ALGORITHM": "HS256",
                "JWT_EXPIRE_MINUTES": "30",
            },
            clear=True,
        ):
            configured = create_sqlite_enterprise_app(workflow_factory=factory)
        with patch.dict(os.environ, {}, clear=True):
            legacy = create_sqlite_enterprise_app(workflow_factory=factory)

        self.assertIsInstance(configured.state.authentication_provider, JWTProvider)
        self.assertIsNone(legacy.state.authentication_provider)


if __name__ == "__main__":
    unittest.main()
