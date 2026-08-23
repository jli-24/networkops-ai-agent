"""Domain pack contract and registry (platform runtime, zero domain code)."""

from network_agent_rag.packs.models import (
    DomainPackSpec,
    KnowledgeCollectionSpec,
    LifecycleSpec,
    LifecycleStatus,
    PermissionSpec,
    RoleSpec,
    RouterSpec,
    UiExtensionSpec,
)
from network_agent_rag.packs.registry import PackRegistrationError, PackRegistry

__all__ = [
    "DomainPackSpec",
    "KnowledgeCollectionSpec",
    "LifecycleSpec",
    "LifecycleStatus",
    "PackRegistrationError",
    "PackRegistry",
    "PermissionSpec",
    "RoleSpec",
    "RouterSpec",
    "UiExtensionSpec",
]
