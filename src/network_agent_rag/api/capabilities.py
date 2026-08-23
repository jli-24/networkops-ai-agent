"""Platform-level capability listing API (pack-agnostic projection)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from network_agent_rag.auth import Permission
from network_agent_rag.auth.dependencies import require_permission
from network_agent_rag.capability import CapabilityRegistry, CapabilityType


_require_embedded_read = require_permission(Permission.EMBEDDED_READ)


class CapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    type: str
    permission: str
    backend: str
    enabled: bool
    metadata: dict[str, Any]


def create_capabilities_router(
    capability_registry: CapabilityRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/capabilities", tags=["capabilities"])

    @router.get("", response_model=list[CapabilityResponse])
    def list_capabilities(
        _: Annotated[None, Depends(_require_embedded_read)],
        type: CapabilityType | None = None,
        permission: Permission | None = None,
    ) -> list[CapabilityResponse]:
        capabilities = capability_registry.list(
            type=type, permission=permission, enabled=True
        )
        return [
            CapabilityResponse(
                name=capability.name,
                version=capability.version,
                type=capability.type.value,
                permission=capability.permission.value,
                backend=capability.backend,
                enabled=capability.enabled,
                metadata=capability.metadata,
            )
            for capability in capabilities
        ]

    return router


__all__ = ["CapabilityResponse", "create_capabilities_router"]
