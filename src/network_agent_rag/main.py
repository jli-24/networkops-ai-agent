"""FastAPI application entry point.

Assembly layer: the only core module allowed to import domain packs. It
mounts the routers of enabled packs (resolved from their manifests) plus
the platform-level health and capability routers. The network chat
workflow is injected via ``app.state.agent_workflow`` by deployment
factories resolved through the pack manifest.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import APIRouter, FastAPI
from langgraph.graph.state import CompiledStateGraph

from network_agent_rag.api.capabilities import create_capabilities_router
from network_agent_rag.core.config import Settings
from network_agent_rag.packs.embeddedops.api import (
    EmbeddedServices,
    create_embedded_router,
)
from network_agent_rag.packs.networkops.api.history import InMemoryHistoryStore
from network_agent_rag.packs.networkops import register_networkops_pack

_health_router = APIRouter()


@_health_router.get("/health")
async def health() -> dict[str, str]:
    """Platform-level liveness probe (pack-independent)."""

    return {"status": "ok"}


def create_app(
    agent_workflow: CompiledStateGraph | None = None,
    history_store: InMemoryHistoryStore | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    embedded_services: EmbeddedServices | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    settings = Settings()
    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.state.agent_workflow = agent_workflow
    application.state.history_store = (
        InMemoryHistoryStore() if history_store is None else history_store
    )
    services = embedded_services or EmbeddedServices()
    register_networkops_pack(services.pack_registry)
    application.include_router(_health_router, prefix=settings.api_prefix)
    for pack in services.pack_registry.list(enabled=True):
        for router_spec in pack.api_routers:
            application.include_router(
                _resolve_router(router_spec.factory, services),
                prefix=settings.api_prefix,
            )
    application.include_router(
        create_capabilities_router(services.capability_registry),
        prefix=settings.api_prefix,
    )
    return application


def _resolve_router(factory_path: str, services: EmbeddedServices):
    """Resolve a pack router reference declared in its manifest.

    Accepts either a factory callable (embedded-style, receives services
    when it is the embedded router factory) or an APIRouter instance
    (network chat surface, exported ready-made).
    """

    import importlib

    from fastapi import APIRouter

    module_name, _, attribute = factory_path.partition(":")
    target = getattr(importlib.import_module(module_name), attribute)
    if isinstance(target, APIRouter):
        return target
    if target is create_embedded_router:
        return target(services)
    return target()


app = create_app()
