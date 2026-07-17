"""JWT token creation and verification."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import TYPE_CHECKING
from uuid import uuid4

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from network_agent_rag.auth.users import UserIdentity

if TYPE_CHECKING:
    from network_agent_rag.auth.identity import TokenPair


class AuthenticationError(PermissionError):
    """Raised when request authentication cannot establish an identity."""

    def __init__(self) -> None:
        super().__init__("authentication failed")


class JWTTokenManager:
    def __init__(
        self,
        secret_key: str,
        *,
        algorithm: str = "HS256",
        expire_minutes: int = 30,
        access_expire_minutes: int = 15,
        refresh_expire_days: int = 7,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(secret_key, str) or len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT secret key must contain at least 32 bytes")
        if algorithm != "HS256":
            raise ValueError("JWT algorithm must be HS256")
        _positive_integer(expire_minutes, "JWT expiration must be a positive number of minutes")
        _positive_integer(
            access_expire_minutes,
            "JWT access expiration must be a positive number of minutes",
        )
        _positive_integer(
            refresh_expire_days,
            "JWT refresh expiration must be a positive number of days",
        )
        self._secret_key = secret_key
        self.algorithm = algorithm
        self.expire_minutes = expire_minutes
        self.access_expire_minutes = access_expire_minutes
        self.refresh_expire_days = refresh_expire_days
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_token(self, user_identity: UserIdentity) -> str:
        """Create the unchanged v0.9 legacy access token."""

        if not isinstance(user_identity, UserIdentity):
            raise TypeError("user_identity must be UserIdentity")
        issued_at = self._now()
        payload: dict[str, object] = {
            "sub": user_identity.user_id,
            "username": user_identity.username,
            "roles": [role.value for role in user_identity.roles],
            "iat": int(issued_at.timestamp()),
            "exp": int(
                (issued_at + timedelta(minutes=self.expire_minutes)).timestamp()
            ),
        }
        if user_identity.email is not None:
            payload["email"] = user_identity.email
        return jwt.encode(payload, self._secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> UserIdentity:
        """Verify a legacy token while rejecting new refresh tokens."""

        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError()
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self.algorithm],
                options={
                    "require": ["sub", "username", "roles", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            _validate_dates(payload, self._now())
            if "sid" in payload or payload.get("typ") in {"access", "refresh"}:
                raise AuthenticationError()
            return _identity_from_payload(payload)
        except AuthenticationError:
            raise
        except (InvalidTokenError, KeyError, TypeError, ValueError, ValidationError):
            raise AuthenticationError() from None

    def create_session_tokens(
        self,
        user_identity: UserIdentity,
        session_id: str,
    ) -> "TokenPair":
        """Create one session-bound access/refresh pair."""

        from network_agent_rag.auth.identity import TokenPair

        if not isinstance(user_identity, UserIdentity):
            raise TypeError("user_identity must be UserIdentity")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        identifier = session_id.strip()
        issued_at = self._now()
        access_expires_at = issued_at + timedelta(minutes=self.access_expire_minutes)
        refresh_expires_at = issued_at + timedelta(days=self.refresh_expire_days)
        common = _identity_claims(user_identity)
        common.update({"iat": int(issued_at.timestamp()), "sid": identifier})
        access = jwt.encode(
            {
                **common,
                "typ": "access",
                "exp": int(access_expires_at.timestamp()),
            },
            self._secret_key,
            algorithm=self.algorithm,
        )
        refresh = jwt.encode(
            {
                **common,
                "typ": "refresh",
                "jti": uuid4().hex,
                "exp": int(refresh_expires_at.timestamp()),
            },
            self._secret_key,
            algorithm=self.algorithm,
        )
        return TokenPair(
            session_id=identifier,
            access_token=access,
            refresh_token=refresh,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )

    def verify_access_token(self, token: str) -> tuple[UserIdentity, str]:
        payload = self._verify_session_token(token, "access", require_jti=False)
        return _identity_from_payload(payload), str(payload["sid"])

    def verify_refresh_token(self, token: str) -> tuple[UserIdentity, str, str]:
        payload = self._verify_session_token(token, "refresh", require_jti=True)
        return (
            _identity_from_payload(payload),
            str(payload["sid"]),
            str(payload["jti"]),
        )

    def _verify_session_token(
        self,
        token: str,
        expected_type: str,
        *,
        require_jti: bool,
    ) -> dict[str, object]:
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError()
        required = ["sub", "username", "roles", "iat", "exp", "typ", "sid"]
        if require_jti:
            required.append("jti")
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self.algorithm],
                options={
                    "require": required,
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            _validate_dates(payload, self._now())
            if payload["typ"] != expected_type:
                raise AuthenticationError()
            for field in ("sid", "jti") if require_jti else ("sid",):
                if not isinstance(payload[field], str) or not payload[field].strip():
                    raise AuthenticationError()
            return payload
        except AuthenticationError:
            raise
        except (InvalidTokenError, KeyError, TypeError, ValueError):
            raise AuthenticationError() from None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("JWT clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _identity_claims(identity: UserIdentity) -> dict[str, object]:
    claims: dict[str, object] = {
        "sub": identity.user_id,
        "username": identity.username,
        "roles": [role.value for role in identity.roles],
    }
    if identity.email is not None:
        claims["email"] = identity.email
    return claims


def _identity_from_payload(payload: dict[str, object]) -> UserIdentity:
    try:
        roles = payload["roles"]
        if not isinstance(roles, list):
            raise AuthenticationError()
        return UserIdentity(
            user_id=payload["sub"],
            username=payload["username"],
            email=payload.get("email"),
            roles=tuple(roles),
        )
    except AuthenticationError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AuthenticationError() from None


def _validate_dates(payload: dict[str, object], now: datetime) -> None:
    issued_at = _numeric_date(payload["iat"])
    expires_at = _numeric_date(payload["exp"])
    current = now.timestamp()
    if issued_at > current or issued_at >= expires_at or current >= expires_at:
        raise AuthenticationError()


def _positive_integer(value: object, message: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(message)


def _numeric_date(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthenticationError()
    numeric = float(value)
    if not isfinite(numeric):
        raise AuthenticationError()
    return numeric


__all__ = ["AuthenticationError", "JWTTokenManager"]
