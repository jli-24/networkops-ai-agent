"""FastAPI application entry point."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from langgraph.graph.state import CompiledStateGraph

from network_agent_rag.api.embedded import (
    EmbeddedServices,
    create_capabilities_router,
    create_embedded_router,
)
from network_agent_rag.api.history import InMemoryHistoryStore
from network_agent_rag.api.router import api_router
from network_agent_rag.core.config import Settings


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
    application.include_router(
        create_embedded_router(services), prefix=settings.api_prefix
    )
    application.include_router(
        create_capabilities_router(services), prefix=settings.api_prefix
    )
    return application


app = create_app()
