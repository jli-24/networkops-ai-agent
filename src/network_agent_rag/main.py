"""FastAPI application entry point.

Assembly layer: the only core module allowed to import domain packs. It
mounts the routers of enabled packs (resolved from their manifests) plus
the platform-level routers. The network domain still mounts directly and
moves onto the same pipeline in v0.16 step 3/4.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from langgraph.graph.state import CompiledStateGraph

from network_agent_rag.api.capabilities import create_capabilities_router
from network_agent_rag.api.history import InMemoryHistoryStore
from network_agent_rag.api.router import api_router
from network_agent_rag.core.config import Settings
from network_agent_rag.packs.embeddedops.api import (
    EmbeddedServices,
    create_embedded_router,
)


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
    application.include_router(api_router, prefix=settings.api_prefix)
    services = embedded_services or EmbeddedServices()
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
    """Resolve a pack router factory declared in its manifest."""

    import importlib

    module_name, _, attribute = factory_path.partition(":")
    factory = getattr(importlib.import_module(module_name), attribute)
    if factory is create_embedded_router:
        return factory(services)
    return factory()


app = create_app()
