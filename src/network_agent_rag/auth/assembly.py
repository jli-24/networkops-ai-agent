"""Authentication-layer lifecycle assembly."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from redis import Redis

from network_agent_rag.auth.authentication import (
    APIKeyProvider,
    CompositeAuthenticationProvider,
    JWTProvider,
)
from network_agent_rag.auth.identity import EnterpriseIdentityService, RedisIdentityStore
from network_agent_rag.auth.token import JWTTokenManager


@contextmanager
def open_redis_identity_authentication(
    redis_url: str,
    token_manager: JWTTokenManager,
    *,
    audit_log: Any = None,
) -> Iterator[CompositeAuthenticationProvider]:
    """Own the Identity Redis client and expose only an authentication provider."""

    if not isinstance(redis_url, str) or not redis_url.strip():
        raise ValueError("IDENTITY_REDIS_URL is required")
    client = Redis.from_url(redis_url.strip(), decode_responses=True)
    try:
        client.ping()
        service = EnterpriseIdentityService(
            token_manager,
            RedisIdentityStore(client),
            audit_log=audit_log,
        )
        yield CompositeAuthenticationProvider(
            JWTProvider(token_manager, identity_service=service),
            APIKeyProvider(service),
        )
    finally:
        client.close()


__all__ = ["open_redis_identity_authentication"]
