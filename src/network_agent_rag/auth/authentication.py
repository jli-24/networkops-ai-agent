"""Request authentication provider contracts."""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from network_agent_rag.auth.identity import EnterpriseIdentityService
from network_agent_rag.auth.token import AuthenticationError, JWTTokenManager
from network_agent_rag.auth.users import UserIdentity


class AuthenticationProvider(Protocol):
    def authenticate(self, request: Request) -> UserIdentity: ...


class JWTProvider:
    def __init__(
        self,
        token_manager: JWTTokenManager,
        *,
        identity_service: EnterpriseIdentityService | None = None,
    ) -> None:
        self.token_manager = token_manager
        self.identity_service = identity_service

    def authenticate(self, request: Request) -> UserIdentity:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            raise AuthenticationError()
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthenticationError()
        if self.identity_service is not None:
            return self.identity_service.authenticate_access_token(parts[1])
        return self.token_manager.verify_token(parts[1])


class APIKeyProvider:
    def __init__(self, identity_service: EnterpriseIdentityService) -> None:
        self.identity_service = identity_service

    def authenticate(self, request: Request) -> UserIdentity:
        api_key = request.headers.get("X-API-Key")
        if api_key is None or not api_key.strip():
            raise AuthenticationError()
        return self.identity_service.authenticate_api_key(api_key.strip())


class CompositeAuthenticationProvider:
    """Select exactly one credential transport without changing RBAC."""

    def __init__(
        self,
        jwt_provider: JWTProvider,
        api_key_provider: APIKeyProvider,
    ) -> None:
        self.jwt_provider = jwt_provider
        self.api_key_provider = api_key_provider

    def authenticate(self, request: Request) -> UserIdentity:
        has_bearer = request.headers.get("Authorization") is not None
        has_api_key = request.headers.get("X-API-Key") is not None
        if has_bearer == has_api_key:
            raise AuthenticationError()
        if has_bearer:
            return self.jwt_provider.authenticate(request)
        return self.api_key_provider.authenticate(request)


__all__ = [
    "APIKeyProvider",
    "AuthenticationProvider",
    "CompositeAuthenticationProvider",
    "JWTProvider",
]
