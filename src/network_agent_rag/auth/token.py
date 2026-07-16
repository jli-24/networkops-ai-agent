"""JWT token creation and verification."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import isfinite

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from network_agent_rag.auth.users import UserIdentity


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(secret_key, str) or len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT secret key must contain at least 32 bytes")
        if algorithm != "HS256":
            raise ValueError("JWT algorithm must be HS256")
        if (
            not isinstance(expire_minutes, int)
            or isinstance(expire_minutes, bool)
            or expire_minutes <= 0
        ):
            raise ValueError("JWT expiration must be a positive number of minutes")
        self._secret_key = secret_key
        self.algorithm = algorithm
        self.expire_minutes = expire_minutes
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_token(self, user_identity: UserIdentity) -> str:
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
            issued_at = _numeric_date(payload["iat"])
            expires_at = _numeric_date(payload["exp"])
            now = self._now().timestamp()
            if issued_at > now or issued_at >= expires_at or now >= expires_at:
                raise AuthenticationError()
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
        except (InvalidTokenError, KeyError, TypeError, ValueError, ValidationError):
            raise AuthenticationError() from None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("JWT clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _numeric_date(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthenticationError()
    numeric = float(value)
    if not isfinite(numeric):
        raise AuthenticationError()
    return numeric


__all__ = ["AuthenticationError", "JWTTokenManager"]
