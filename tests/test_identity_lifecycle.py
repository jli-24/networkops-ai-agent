"""Enterprise identity lifecycle tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest

import jwt
from pydantic import ValidationError
from starlette.requests import Request
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.auth import (
    APIKeyCredential,
    APIKeyProvider,
    AuthenticationError,
    CompositeAuthenticationProvider,
    EnterpriseIdentityService,
    IdentitySession,
    IdentityStore,
    InMemoryIdentityStore,
    JWTProvider,
    JWTTokenManager,
    RedisIdentityStore,
    Role,
    SessionStatus,
    TokenPair,
    UserIdentity,
)
from network_agent_rag.api.enterprise import (
    create_enterprise_app,
    create_storage_enterprise_app,
)
from network_agent_rag.core.config import Settings
from deployment.app import validate_production_settings
from tests.test_checkpoint import build_graph


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SECRET = "a" * 32


def identity(role: Role = Role.ENGINEER) -> UserIdentity:
    return UserIdentity(
        user_id="engineer-1",
        username="noc-engineer",
        roles=(role,),
    )


def manager(clock: dict[str, datetime] | None = None) -> JWTTokenManager:
    current = clock or {"now": NOW}
    return JWTTokenManager(
        SECRET,
        clock=lambda: current["now"],
        access_expire_minutes=15,
        refresh_expire_days=7,
    )


def request(*headers: tuple[bytes, bytes]) -> Request:
    return Request({"type": "http", "path_params": {}, "headers": list(headers)})


class IdentityModelTests(unittest.TestCase):
    def test_models_are_strict_frozen_and_do_not_expose_credentials(self) -> None:
        session = IdentitySession(
            session_id=" session-1 ",
            identity=identity(),
            created_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
        credential = APIKeyCredential(
            key_id=" key-1 ",
            identity=identity(),
            created_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )

        self.assertEqual(session.session_id, "session-1")
        self.assertEqual(credential.key_id, "key-1")
        self.assertEqual(session.status, SessionStatus.ACTIVE)
        serialized = repr(
            {
                "session": session.model_dump(mode="json"),
                "credential": credential.model_dump(mode="json"),
            }
        ).lower()
        for forbidden in ("token", "secret", "hash", "fingerprint"):
            self.assertNotIn(forbidden, serialized)
        with self.assertRaises(ValidationError):
            session.status = SessionStatus.REVOKED
        with self.assertRaises(ValidationError):
            IdentitySession(
                session_id=" ",
                identity=identity(),
                created_at=NOW,
                expires_at=NOW,
                extra="forbidden",
            )
        with self.assertRaises(ValidationError):
            TokenPair(
                session_id="session-1",
                access_token="",
                refresh_token="",
                access_expires_at=NOW,
                refresh_expires_at=NOW,
            )


class SessionTokenTests(unittest.TestCase):
    def test_legacy_token_contract_remains_unchanged(self) -> None:
        token_manager = manager()
        token = token_manager.create_token(identity())
        payload = jwt.decode(token, options={"verify_signature": False})

        self.assertEqual(token_manager.verify_token(token), identity())
        self.assertEqual(
            set(payload),
            {"sub", "username", "roles", "iat", "exp"},
        )
        self.assertNotIn("typ", payload)
        self.assertNotIn("sid", payload)

    def test_session_tokens_are_typed_and_refresh_cannot_be_used_as_access(self) -> None:
        token_manager = manager()
        pair = token_manager.create_session_tokens(identity(), "session-1")
        access = pair.access_token.get_secret_value()
        refresh = pair.refresh_token.get_secret_value()
        access_payload = jwt.decode(access, options={"verify_signature": False})
        refresh_payload = jwt.decode(refresh, options={"verify_signature": False})

        self.assertIsInstance(pair, TokenPair)
        self.assertEqual(access_payload["typ"], "access")
        self.assertEqual(refresh_payload["typ"], "refresh")
        self.assertEqual(access_payload["sid"], "session-1")
        self.assertIn("jti", refresh_payload)
        self.assertEqual(token_manager.verify_access_token(access)[0], identity())
        with self.assertRaises(AuthenticationError):
            token_manager.verify_token(access)
        with self.assertRaises(AuthenticationError):
            token_manager.verify_access_token(refresh)
        with self.assertRaises(AuthenticationError):
            token_manager.verify_token(refresh)

        invalid_claims = jwt.encode(
            {
                **access_payload,
                "roles": ["engineer"],
            },
            SECRET,
            algorithm="HS256",
        )
        with self.assertRaises(AuthenticationError):
            token_manager.verify_access_token(invalid_claims)


class IdentityServiceTests(unittest.TestCase):
    def test_session_refresh_rotation_and_reuse_revoke_the_session(self) -> None:
        store = InMemoryIdentityStore()
        service = EnterpriseIdentityService(manager(), store, clock=lambda: NOW)
        first = service.issue_session(identity())
        self.assertIsInstance(store, IdentityStore)
        self.assertEqual(service.authenticate_access_token(first.access_token.get_secret_value()), identity())

        second = service.refresh(first.refresh_token.get_secret_value())
        self.assertEqual(service.get_session(first.session_id).status, SessionStatus.ACTIVE)
        with self.assertRaises(AuthenticationError):
            service.refresh(first.refresh_token.get_secret_value())
        self.assertEqual(service.get_session(first.session_id).status, SessionStatus.REVOKED)
        with self.assertRaises(AuthenticationError):
            service.authenticate_access_token(second.access_token.get_secret_value())

    def test_logout_and_expiration_only_change_authentication_state(self) -> None:
        current = {"now": NOW}
        token_manager = manager(current)
        service = EnterpriseIdentityService(
            token_manager,
            InMemoryIdentityStore(),
            clock=lambda: current["now"],
        )
        pair = service.issue_session(identity())
        service.logout(pair.session_id)
        self.assertEqual(service.get_session(pair.session_id).status, SessionStatus.REVOKED)
        self.assertEqual(identity().roles, (Role.ENGINEER,))
        with self.assertRaises(AuthenticationError):
            service.authenticate_access_token(pair.access_token.get_secret_value())

    def test_api_key_is_bound_to_existing_identity_and_revocation_does_not_change_rbac(self) -> None:
        service = EnterpriseIdentityService(manager(), InMemoryIdentityStore(), clock=lambda: NOW)
        original = identity(Role.ADMIN)
        credential, raw_key = service.create_api_key(
            original,
            expires_at=NOW + timedelta(days=1),
        )

        authenticated = service.authenticate_api_key(raw_key.get_secret_value())
        self.assertEqual(authenticated, original)
        self.assertIs(type(authenticated), UserIdentity)
        service.revoke_api_key(credential.key_id)
        self.assertEqual(original.roles, (Role.ADMIN,))
        with self.assertRaises(AuthenticationError):
            service.authenticate_api_key(raw_key.get_secret_value())

    def test_identity_audit_contains_references_but_no_credentials_or_hashes(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            service = EnterpriseIdentityService(
                manager(),
                InMemoryIdentityStore(),
                audit_log=audit,
                clock=lambda: NOW,
            )
            pair = service.issue_session(identity())
            credential, raw_key = service.create_api_key(identity())
            service.authenticate_api_key(raw_key.get_secret_value())
            with self.assertRaises(AuthenticationError):
                service.authenticate_api_key("invalid")
            service.logout(pair.session_id)
            events = audit.list_all_events(event_type=AuditEventType.DECISION)

        actions = [event.action for event in events]
        self.assertIn("api_key_authenticate_success", actions)
        self.assertIn("api_key_authenticate_failed", actions)
        self.assertIn("logout", actions)
        serialized = repr([event.model_dump(mode="json") for event in events]).lower()
        self.assertIn(pair.session_id, serialized)
        self.assertIn(credential.key_id, serialized)
        for forbidden in (
            raw_key.get_secret_value().lower(),
            pair.access_token.get_secret_value().lower(),
            pair.refresh_token.get_secret_value().lower(),
            "token_hash",
            "api_key_hash",
            "refresh_hash",
            "secret_hash",
            "fingerprint",
            "noc-engineer",
        ):
            self.assertNotIn(forbidden, serialized)


class AuthenticationProviderTests(unittest.TestCase):
    def test_api_key_and_bearer_share_user_identity_and_reject_ambiguous_credentials(self) -> None:
        service = EnterpriseIdentityService(manager(), InMemoryIdentityStore(), clock=lambda: NOW)
        pair = service.issue_session(identity())
        _, raw_key = service.create_api_key(identity())
        provider = CompositeAuthenticationProvider(
            JWTProvider(manager(), identity_service=service),
            APIKeyProvider(service),
        )

        bearer = request(
            (b"authorization", f"Bearer {pair.access_token.get_secret_value()}".encode())
        )
        api_key = request((b"x-api-key", raw_key.get_secret_value().encode()))
        ambiguous = request(
            (b"authorization", f"Bearer {pair.access_token.get_secret_value()}".encode()),
            (b"x-api-key", raw_key.get_secret_value().encode()),
        )

        self.assertEqual(provider.authenticate(bearer), identity())
        self.assertEqual(provider.authenticate(api_key), identity())
        with self.assertRaises(AuthenticationError):
            provider.authenticate(ambiguous)

    def test_api_key_uses_existing_rbac_and_identity_never_enters_sse(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            service = EnterpriseIdentityService(
                manager(),
                InMemoryIdentityStore(),
                audit_log=audit,
                clock=lambda: NOW,
            )
            _, engineer_key = service.create_api_key(identity(Role.ENGINEER))
            _, admin_key = service.create_api_key(identity(Role.ADMIN))
            provider = CompositeAuthenticationProvider(
                JWTProvider(manager(), identity_service=service),
                APIKeyProvider(service),
            )
            app = create_enterprise_app(
                agent_workflow=build_graph(InMemorySaver(), audit, []),
                audit_log=audit,
                clock=lambda: NOW,
                authentication_provider=provider,
            )
            with TestClient(app) as client:
                engineer = client.post(
                    "/api/v1/incidents",
                    headers={"X-API-Key": engineer_key.get_secret_value()},
                    json={
                        "session_id": "chat-session",
                        "incident_id": "INC-API-KEY",
                        "query": "repair link",
                    },
                )
                admin = client.post(
                    "/api/v1/incidents",
                    headers={"X-API-Key": admin_key.get_secret_value()},
                    json={"session_id": "chat-session", "query": "repair link"},
                )

        self.assertEqual(engineer.status_code, 200)
        self.assertEqual(admin.status_code, 403)
        serialized = engineer.text.lower()
        for forbidden in (
            "useridentity",
            "usercontext",
            "identitysession",
            "noc-engineer",
            engineer_key.get_secret_value().lower(),
        ):
            self.assertNotIn(forbidden, serialized)


class RedisIdentityStoreTests(unittest.TestCase):
    def test_rotation_uses_only_the_identity_namespace(self) -> None:
        client = Mock()
        client.eval.return_value = 1
        store = RedisIdentityStore(client)

        rotated = store.rotate_refresh(
            "session-1",
            expected_hash="old",
            new_hash="new",
            expires_at=NOW + timedelta(days=7),
        )

        self.assertTrue(rotated)
        arguments = client.eval.call_args.args
        serialized = repr(arguments).lower()
        self.assertIn("networkops:identity:v1:", serialized)
        self.assertNotIn("checkpoint", serialized)
        self.assertNotIn("langgraph", serialized)


class IdentityConfigurationTests(unittest.TestCase):
    def test_identity_settings_are_independent_from_checkpoint_redis(self) -> None:
        settings = Settings(
            _env_file=None,
            identity_redis_url="redis://identity/1",
            redis_url="redis://checkpoint/0",
            jwt_access_expire_minutes=12,
            jwt_refresh_expire_days=5,
        )

        self.assertEqual(settings.identity_redis_url, "redis://identity/1")
        self.assertEqual(settings.redis_url, "redis://checkpoint/0")
        self.assertEqual(settings.jwt_access_expire_minutes, 12)
        self.assertEqual(settings.jwt_refresh_expire_days, 5)
        self.assertEqual(settings.jwt_expire_minutes, 30)

    def test_production_requires_separate_identity_redis(self) -> None:
        values = {
            "environment": "production",
            "storage_backend": "postgres",
            "checkpoint_backend": "redis",
            "database_url": "postgresql://database",
            "redis_url": "redis://checkpoint/0",
            "identity_redis_url": "redis://identity/1",
            "jwt_secret_key": SECRET,
            "networkops_workflow_factory": "module:factory",
        }
        validate_production_settings(Settings(_env_file=None, **values))

        for override in (
            {"identity_redis_url": None},
            {"identity_redis_url": "redis://checkpoint/0"},
        ):
            with self.subTest(override=override), self.assertRaises(RuntimeError):
                validate_production_settings(
                    Settings(_env_file=None, **{**values, **override})
                )

    def test_storage_factory_uses_auth_layer_assembly_without_exposing_store(self) -> None:
        marker = JWTProvider(manager())
        observed: dict[str, object] = {}

        @contextmanager
        def open_identity(redis_url, token_manager, *, audit_log):
            observed.update(
                redis_url=redis_url,
                token_manager=token_manager,
                audit_log=audit_log,
            )
            yield marker

        with TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"JWT_SECRET_KEY": SECRET},
            clear=True,
        ), patch(
            "network_agent_rag.api.enterprise.open_redis_identity_authentication",
            side_effect=open_identity,
        ):
            root = Path(directory)
            app = create_storage_enterprise_app(
                workflow_factory=lambda checkpointer, audit_log: build_graph(
                    checkpointer, audit_log, []
                ),
                identity_redis_url="redis://identity/1",
                checkpoint_path=root / "checkpoint.sqlite3",
                audit_path=root / "audit.sqlite3",
                observability_path=root / "trace.sqlite3",
                benchmark_results_path=root / "benchmarks",
            )
            with TestClient(app):
                self.assertIs(app.state.authentication_provider, marker)

        self.assertEqual(observed["redis_url"], "redis://identity/1")
        self.assertIsInstance(observed["token_manager"], JWTTokenManager)
        self.assertIsInstance(observed["audit_log"], SQLiteAuditLog)
        self.assertFalse(hasattr(app.state, "identity_store"))


if __name__ == "__main__":
    unittest.main()
