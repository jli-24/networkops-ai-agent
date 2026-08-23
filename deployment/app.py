"""Production ASGI factory without a bundled business workflow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import asyncio
import importlib

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import psycopg
from redis import Redis

from network_agent_rag.packs.networkops.api.enterprise import create_storage_enterprise_app
from network_agent_rag.core.config import Settings
from network_agent_rag.auth import JWTTokenManager
from network_agent_rag.observability.deployment import (
    DeploymentMetricsMiddleware,
    RequestMetrics,
)


Probe = Callable[[str], bool]


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create the storage-backed app from an operator-provided workflow factory."""

    resolved = settings or Settings()
    if resolved.environment == "production":
        validate_production_settings(resolved)
    factory_path = resolved.networkops_workflow_factory
    if not factory_path:
        raise RuntimeError("workflow factory is required for deployment")
    workflow_factory = load_workflow_factory(factory_path)
    identity_token_manager = None
    if resolved.identity_redis_url:
        if resolved.jwt_secret_key is None:
            raise RuntimeError("identity authentication configuration is incomplete")
        identity_token_manager = JWTTokenManager(
            resolved.jwt_secret_key.get_secret_value(),
            algorithm=resolved.jwt_algorithm,
            expire_minutes=resolved.jwt_expire_minutes,
            access_expire_minutes=resolved.jwt_access_expire_minutes,
            refresh_expire_days=resolved.jwt_refresh_expire_days,
        )
    factory_argument = (
        {"policy_workflow_factory": workflow_factory}
        if resolved.policy_engine_enabled
        else {"observed_workflow_factory": workflow_factory}
    )
    application = create_storage_enterprise_app(
        **factory_argument,
        storage_backend=resolved.storage_backend,
        checkpoint_backend=resolved.checkpoint_backend,
        database_url=resolved.database_url,
        redis_url=resolved.redis_url,
        identity_redis_url=resolved.identity_redis_url,
        identity_token_manager=identity_token_manager,
    )
    install_deployment_features(application, resolved)
    return application


def validate_production_settings(settings: Settings) -> None:
    """Fail closed instead of silently starting a development deployment."""

    valid = (
        settings.storage_backend == "postgres"
        and settings.checkpoint_backend == "redis"
        and bool(settings.database_url)
        and bool(settings.redis_url)
        and bool(settings.identity_redis_url)
        and settings.identity_redis_url != settings.redis_url
        and settings.jwt_secret_key is not None
        and bool(settings.networkops_workflow_factory)
        and settings.policy_engine_enabled
    )
    if not valid:
        raise RuntimeError("invalid production deployment configuration")


def load_workflow_factory(path: str) -> Callable[..., Any]:
    """Load a trusted deployment callable from `module:attribute`."""

    if not isinstance(path, str) or path.count(":") != 1:
        raise RuntimeError("workflow factory cannot be loaded")
    module_name, attribute = (item.strip() for item in path.split(":", 1))
    if not module_name or not attribute:
        raise RuntimeError("workflow factory cannot be loaded")
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError):
        raise RuntimeError("workflow factory cannot be loaded") from None
    if not callable(factory):
        raise RuntimeError("workflow factory must be callable")
    return factory


def install_deployment_features(
    application: FastAPI,
    settings: Settings,
    *,
    database_probe: Probe | None = None,
    redis_probe: Probe | None = None,
) -> None:
    """Attach readiness and production-only metric exposition."""

    check_database = database_probe or _probe_postgres
    check_redis = redis_probe or _probe_redis

    async def readiness() -> JSONResponse:
        database_status = "ok"
        redis_status = "not_configured"
        if settings.storage_backend == "postgres" or settings.checkpoint_backend == "postgres":
            database_status = await _check(check_database, settings.database_url)
        if settings.checkpoint_backend == "redis" or settings.identity_redis_url:
            urls = [
                url
                for url in (settings.redis_url, settings.identity_redis_url)
                if url is not None
            ]
            checks = [await _check(check_redis, url) for url in dict.fromkeys(urls)]
            redis_status = "ok" if checks and all(item == "ok" for item in checks) else "unavailable"
        ready = database_status != "unavailable" and redis_status != "unavailable"
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "service": "ok",
                "database": database_status,
                "redis": redis_status,
            },
        )

    application.add_api_route(
        "/health",
        readiness,
        methods=["GET"],
        include_in_schema=False,
    )
    application.add_middleware(
        DeploymentMetricsMiddleware,
        enabled=settings.prometheus_enabled,
        registry=RequestMetrics(),
    )


async def _check(probe: Probe, url: str | None) -> str:
    if not url:
        return "unavailable"
    try:
        return "ok" if await asyncio.to_thread(probe, url) else "unavailable"
    except Exception:
        return "unavailable"


def _probe_postgres(url: str) -> bool:
    with psycopg.connect(url, connect_timeout=2) as connection:
        connection.execute("SELECT 1")
    return True


def _probe_redis(url: str) -> bool:
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        return bool(client.ping())
    finally:
        client.close()


__all__ = [
    "create_app",
    "install_deployment_features",
    "load_workflow_factory",
    "validate_production_settings",
]
