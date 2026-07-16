"""Request authentication provider contracts."""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from network_agent_rag.auth.token import AuthenticationError, JWTTokenManager
from network_agent_rag.auth.users import UserIdentity


class AuthenticationProvider(Protocol):
    def authenticate(self, request: Request) -> UserIdentity: ...


class JWTProvider:
    def __init__(self, token_manager: JWTTokenManager) -> None:
        self.token_manager = token_manager

    def authenticate(self, request: Request) -> UserIdentity:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            raise AuthenticationError()
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthenticationError()
        return self.token_manager.verify_token(parts[1])


__all__ = ["AuthenticationProvider", "JWTProvider"]
