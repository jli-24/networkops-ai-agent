"""Authentication-layer identity lifecycle services and stores."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from threading import RLock
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4
import json

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SecretStr,
    model_validator,
)

from network_agent_rag.audit import AuditEventType
from network_agent_rag.auth.models import NonEmptyString
from network_agent_rag.auth.token import AuthenticationError, JWTTokenManager
from network_agent_rag.auth.users import UserIdentity


class SessionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class IdentitySession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: NonEmptyString
    identity: UserIdentity
    created_at: AwareDatetime
    expires_at: AwareDatetime
    status: SessionStatus = SessionStatus.ACTIVE

    @model_validator(mode="after")
    def validate_lifetime(self) -> "IdentitySession":
        if self.expires_at <= self.created_at:
            raise ValueError("session expiration must follow creation")
        return self


class TokenPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: NonEmptyString
    access_token: SecretStr
    refresh_token: SecretStr
    access_expires_at: AwareDatetime
    refresh_expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_tokens(self) -> "TokenPair":
        if (
            not self.access_token.get_secret_value()
            or not self.refresh_token.get_secret_value()
            or self.refresh_expires_at <= self.access_expires_at
        ):
            raise ValueError("token pair must contain valid credentials and lifetimes")
        return self


class APIKeyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: NonEmptyString
    identity: UserIdentity
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    status: SessionStatus = SessionStatus.ACTIVE

    @model_validator(mode="after")
    def validate_lifetime(self) -> "APIKeyCredential":
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("API key expiration must follow creation")
        return self


@runtime_checkable
class IdentityStore(Protocol):
    def create_session(self, session: IdentitySession, refresh_hash: str) -> None: ...

    def get_session(self, session_id: str) -> IdentitySession: ...

    def rotate_refresh(
        self,
        session_id: str,
        *,
        expected_hash: str,
        new_hash: str,
        expires_at: datetime,
    ) -> bool: ...

    def set_session_status(
        self, session_id: str, status: SessionStatus
    ) -> IdentitySession: ...

    def create_api_key(self, credential: APIKeyCredential, key_hash: str) -> None: ...

    def get_api_key(self, key_id: str) -> tuple[APIKeyCredential, str]: ...

    def set_api_key_status(
        self, key_id: str, status: SessionStatus
    ) -> APIKeyCredential: ...


class InMemoryIdentityStore:
    """Small deterministic identity store for development and tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[IdentitySession, str]] = {}
        self._api_keys: dict[str, tuple[APIKeyCredential, str]] = {}
        self._lock = RLock()

    def create_session(self, session: IdentitySession, refresh_hash: str) -> None:
        with self._lock:
            if session.session_id in self._sessions:
                raise ValueError("session already exists")
            self._sessions[session.session_id] = (session.model_copy(deep=True), refresh_hash)

    def get_session(self, session_id: str) -> IdentitySession:
        with self._lock:
            try:
                session = self._sessions[_required(session_id, "session_id")][0]
            except KeyError:
                raise KeyError("unknown identity session") from None
            return session.model_copy(deep=True)

    def rotate_refresh(
        self,
        session_id: str,
        *,
        expected_hash: str,
        new_hash: str,
        expires_at: datetime,
    ) -> bool:
        identifier = _required(session_id, "session_id")
        with self._lock:
            stored = self._sessions.get(identifier)
            if stored is None or stored[0].status is not SessionStatus.ACTIVE:
                return False
            session, current_hash = stored
            if not compare_digest(current_hash, expected_hash):
                self._sessions[identifier] = (
                    session.model_copy(update={"status": SessionStatus.REVOKED}),
                    "",
                )
                return False
            updated = session.model_copy(update={"expires_at": expires_at})
            self._sessions[identifier] = (updated, new_hash)
            return True

    def set_session_status(
        self, session_id: str, status: SessionStatus
    ) -> IdentitySession:
        identifier = _required(session_id, "session_id")
        with self._lock:
            try:
                session, refresh_hash = self._sessions[identifier]
            except KeyError:
                raise KeyError("unknown identity session") from None
            updated = session.model_copy(update={"status": status})
            self._sessions[identifier] = (updated, refresh_hash)
            return updated.model_copy(deep=True)

    def create_api_key(self, credential: APIKeyCredential, key_hash: str) -> None:
        with self._lock:
            if credential.key_id in self._api_keys:
                raise ValueError("API key already exists")
            self._api_keys[credential.key_id] = (
                credential.model_copy(deep=True),
                key_hash,
            )

    def get_api_key(self, key_id: str) -> tuple[APIKeyCredential, str]:
        with self._lock:
            try:
                credential, key_hash = self._api_keys[_required(key_id, "key_id")]
            except KeyError:
                raise KeyError("unknown API key") from None
            return credential.model_copy(deep=True), key_hash

    def set_api_key_status(
        self, key_id: str, status: SessionStatus
    ) -> APIKeyCredential:
        identifier = _required(key_id, "key_id")
        with self._lock:
            try:
                credential, key_hash = self._api_keys[identifier]
            except KeyError:
                raise KeyError("unknown API key") from None
            updated = credential.model_copy(update={"status": status})
            self._api_keys[identifier] = (updated, key_hash)
            return updated.model_copy(deep=True)


class RedisIdentityStore:
    """Identity-only Redis store using the project's existing Redis client."""

    _NAMESPACE = "networkops:identity:v1:"
    _ROTATE_REFRESH = """
local status = redis.call('HGET', KEYS[1], 'status')
if not status then return -1 end
if status ~= 'active' then return -2 end
local current = redis.call('GET', KEYS[2])
if not current then return -3 end
if current ~= ARGV[1] then
  redis.call('HSET', KEYS[1], 'status', 'revoked')
  redis.call('DEL', KEYS[2])
  return 0
end
redis.call('HSET', KEYS[1], 'expires_at', ARGV[3])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('EXPIREAT', KEYS[1], ARGV[4])
redis.call('EXPIREAT', KEYS[2], ARGV[4])
return 1
"""

    def __init__(self, client: Any) -> None:
        self._client = client

    def create_session(self, session: IdentitySession, refresh_hash: str) -> None:
        key = self._session_key(session.session_id)
        if self._client.exists(key):
            raise ValueError("session already exists")
        pipeline = self._client.pipeline(transaction=True)
        pipeline.hset(key, mapping=_session_mapping(session))
        pipeline.set(self._refresh_key(session.session_id), refresh_hash)
        expires = int(session.expires_at.timestamp())
        pipeline.expireat(key, expires)
        pipeline.expireat(self._refresh_key(session.session_id), expires)
        pipeline.execute()

    def get_session(self, session_id: str) -> IdentitySession:
        values = _decoded_mapping(self._client.hgetall(self._session_key(session_id)))
        if not values:
            raise KeyError("unknown identity session")
        return _session_from_mapping(values)

    def rotate_refresh(
        self,
        session_id: str,
        *,
        expected_hash: str,
        new_hash: str,
        expires_at: datetime,
    ) -> bool:
        result = self._client.eval(
            self._ROTATE_REFRESH,
            2,
            self._session_key(session_id),
            self._refresh_key(session_id),
            expected_hash,
            new_hash,
            expires_at.astimezone(timezone.utc).isoformat(),
            int(expires_at.timestamp()),
        )
        return int(result) == 1

    def set_session_status(
        self, session_id: str, status: SessionStatus
    ) -> IdentitySession:
        key = self._session_key(session_id)
        if not self._client.exists(key):
            raise KeyError("unknown identity session")
        self._client.hset(key, "status", status.value)
        if status is not SessionStatus.ACTIVE:
            self._client.delete(self._refresh_key(session_id))
        return self.get_session(session_id)

    def create_api_key(self, credential: APIKeyCredential, key_hash: str) -> None:
        key = self._api_key(credential.key_id)
        if self._client.exists(key):
            raise ValueError("API key already exists")
        mapping = {
            "key_id": credential.key_id,
            "identity": credential.identity.model_dump_json(),
            "created_at": credential.created_at.isoformat(),
            "expires_at": credential.expires_at.isoformat()
            if credential.expires_at is not None
            else "",
            "status": credential.status.value,
            "key_hash": key_hash,
        }
        self._client.hset(key, mapping=mapping)
        if credential.expires_at is not None:
            self._client.expireat(key, int(credential.expires_at.timestamp()))

    def get_api_key(self, key_id: str) -> tuple[APIKeyCredential, str]:
        values = _decoded_mapping(self._client.hgetall(self._api_key(key_id)))
        if not values:
            raise KeyError("unknown API key")
        credential = APIKeyCredential(
            key_id=values["key_id"],
            identity=UserIdentity.model_validate_json(values["identity"]),
            created_at=datetime.fromisoformat(values["created_at"]),
            expires_at=datetime.fromisoformat(values["expires_at"])
            if values["expires_at"]
            else None,
            status=values["status"],
        )
        return credential, values["key_hash"]

    def set_api_key_status(
        self, key_id: str, status: SessionStatus
    ) -> APIKeyCredential:
        key = self._api_key(key_id)
        if not self._client.exists(key):
            raise KeyError("unknown API key")
        self._client.hset(key, "status", status.value)
        return self.get_api_key(key_id)[0]

    def _session_key(self, session_id: str) -> str:
        return f"{self._NAMESPACE}session:{_required(session_id, 'session_id')}"

    def _refresh_key(self, session_id: str) -> str:
        return f"{self._NAMESPACE}refresh:{_required(session_id, 'session_id')}"

    def _api_key(self, key_id: str) -> str:
        return f"{self._NAMESPACE}api-key:{_required(key_id, 'key_id')}"


class _AuditRecorder(Protocol):
    def record(self, **values: Any) -> Any: ...


class EnterpriseIdentityService:
    """Own all session and API-key lifecycle operations inside auth."""

    def __init__(
        self,
        token_manager: JWTTokenManager,
        store: IdentityStore,
        *,
        audit_log: _AuditRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, IdentityStore):
            raise TypeError("store must implement IdentityStore")
        self._token_manager = token_manager
        self._store = store
        self._audit_log = audit_log
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def issue_session(self, identity: UserIdentity) -> TokenPair:
        session_id = uuid4().hex
        pair = self._token_manager.create_session_tokens(identity, session_id)
        session = IdentitySession(
            session_id=session_id,
            identity=identity,
            created_at=self._now(),
            expires_at=pair.refresh_expires_at,
        )
        self._store.create_session(session, _digest(pair.refresh_token.get_secret_value()))
        return pair

    def refresh(self, refresh_token: str) -> TokenPair:
        try:
            identity, session_id, _ = self._token_manager.verify_refresh_token(refresh_token)
        except AuthenticationError:
            self._audit("refresh_token_failed", "denied", actor_id="anonymous")
            raise
        pair = self._token_manager.create_session_tokens(identity, session_id)
        rotated = self._store.rotate_refresh(
            session_id,
            expected_hash=_digest(refresh_token),
            new_hash=_digest(pair.refresh_token.get_secret_value()),
            expires_at=pair.refresh_expires_at,
        )
        if not rotated:
            self._audit(
                "refresh_token_failed",
                "denied",
                actor_id=identity.user_id,
                session_id=session_id,
            )
            raise AuthenticationError()
        self._audit(
            "refresh_token_success",
            "allowed",
            actor_id=identity.user_id,
            session_id=session_id,
        )
        return pair

    def authenticate_access_token(self, access_token: str) -> UserIdentity:
        identity, session_id = self._token_manager.verify_access_token(access_token)
        session = self.get_session(session_id)
        if session.status is not SessionStatus.ACTIVE or session.identity != identity:
            raise AuthenticationError()
        return identity

    def get_session(self, session_id: str) -> IdentitySession:
        try:
            session = self._store.get_session(session_id)
        except KeyError:
            raise AuthenticationError() from None
        if session.status is SessionStatus.ACTIVE and session.expires_at <= self._now():
            session = self._store.set_session_status(session.session_id, SessionStatus.EXPIRED)
        return session

    def logout(self, session_id: str) -> None:
        session = self._set_session_status(session_id, SessionStatus.REVOKED)
        self._audit(
            "logout",
            "allowed",
            actor_id=session.identity.user_id,
            session_id=session.session_id,
        )

    def revoke_session(self, session_id: str) -> None:
        session = self._set_session_status(session_id, SessionStatus.REVOKED)
        self._audit(
            "session_revoked",
            "allowed",
            actor_id=session.identity.user_id,
            session_id=session.session_id,
        )

    def create_api_key(
        self,
        identity: UserIdentity,
        *,
        expires_at: datetime | None = None,
    ) -> tuple[APIKeyCredential, SecretStr]:
        if not isinstance(identity, UserIdentity):
            raise TypeError("identity must be UserIdentity")
        key_id = uuid4().hex
        raw_key = f"nops.{key_id}.{token_urlsafe(32)}"
        credential = APIKeyCredential(
            key_id=key_id,
            identity=identity,
            created_at=self._now(),
            expires_at=expires_at,
        )
        self._store.create_api_key(credential, _digest(raw_key))
        return credential, SecretStr(raw_key)

    def authenticate_api_key(self, api_key: str) -> UserIdentity:
        try:
            key_id = _api_key_id(api_key)
        except AuthenticationError:
            self._audit(
                "api_key_authenticate_failed",
                "denied",
                actor_id="anonymous",
            )
            raise
        try:
            credential, stored_hash = self._store.get_api_key(key_id)
            allowed = (
                credential.status is SessionStatus.ACTIVE
                and (credential.expires_at is None or credential.expires_at > self._now())
                and compare_digest(stored_hash, _digest(api_key))
            )
            if not allowed:
                raise AuthenticationError()
        except (AuthenticationError, KeyError):
            self._audit(
                "api_key_authenticate_failed",
                "denied",
                actor_id="anonymous",
                key_id=key_id,
            )
            raise AuthenticationError() from None
        self._audit(
            "api_key_authenticate_success",
            "allowed",
            actor_id=credential.identity.user_id,
            key_id=credential.key_id,
        )
        return credential.identity

    def revoke_api_key(self, key_id: str) -> None:
        try:
            self._store.set_api_key_status(key_id, SessionStatus.REVOKED)
        except KeyError:
            raise AuthenticationError() from None

    def _set_session_status(
        self, session_id: str, status: SessionStatus
    ) -> IdentitySession:
        try:
            return self._store.set_session_status(session_id, status)
        except KeyError:
            raise AuthenticationError() from None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("identity clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _audit(
        self,
        action: str,
        decision: str,
        *,
        actor_id: str,
        session_id: str | None = None,
        key_id: str | None = None,
    ) -> None:
        if self._audit_log is None:
            return
        details: dict[str, object] = {
            "actor_id": actor_id,
            "source": "authentication",
            "decision": decision,
        }
        if session_id is not None:
            details["session_id"] = session_id
        if key_id is not None:
            details["key_id"] = key_id
        identity_reference = session_id or key_id or "authentication"
        self._audit_log.record(
            incident_id=f"identity:{identity_reference}",
            event_type=AuditEventType.DECISION,
            actor="identity_service",
            action=action,
            outcome=decision,
            details=details,
        )


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _api_key_id(api_key: str) -> str:
    if not isinstance(api_key, str):
        raise AuthenticationError()
    parts = api_key.split(".")
    if len(parts) != 3 or parts[0] != "nops" or not parts[1] or not parts[2]:
        raise AuthenticationError()
    return parts[1]


def _session_mapping(session: IdentitySession) -> dict[str, str]:
    return {
        "session_id": session.session_id,
        "identity": session.identity.model_dump_json(),
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "status": session.status.value,
    }


def _session_from_mapping(values: dict[str, str]) -> IdentitySession:
    return IdentitySession(
        session_id=values["session_id"],
        identity=UserIdentity.model_validate_json(values["identity"]),
        created_at=datetime.fromisoformat(values["created_at"]),
        expires_at=datetime.fromisoformat(values["expires_at"]),
        status=values["status"],
    )


def _decoded_mapping(values: dict[object, object]) -> dict[str, str]:
    return {
        key.decode() if isinstance(key, bytes) else str(key): value.decode()
        if isinstance(value, bytes)
        else str(value)
        for key, value in values.items()
    }


__all__ = [
    "APIKeyCredential",
    "EnterpriseIdentityService",
    "IdentitySession",
    "IdentityStore",
    "InMemoryIdentityStore",
    "RedisIdentityStore",
    "SessionStatus",
    "TokenPair",
]
