"""EmbeddedOps DomainPack manifest: the single source of truth for pack assets."""

from __future__ import annotations

from network_agent_rag.auth import Permission
from network_agent_rag.packs.embeddedops.capabilities import EMBEDDEDOPS_CAPABILITIES
from network_agent_rag.packs.models import (
    DomainPackSpec,
    KnowledgeCollectionSpec,
    LifecycleSpec,
    PermissionSpec,
    RoleSpec,
    RouterSpec,
    UiExtensionSpec,
)


EMBEDDEDOPS_PACK = DomainPackSpec(
    name="embeddedops",
    version="0.16.0",
    description=(
        "EmbeddedOps domain pack: copilot agents, virtual hardware lab, "
        "validation loop, and embedded knowledge/evaluation assets."
    ),
    lifecycle=LifecycleSpec(),
    capabilities=EMBEDDEDOPS_CAPABILITIES,
    agent_factories=(
        "network_agent_rag.packs.embeddedops.agents.hardware_agent:run_hardware_agent",
        "network_agent_rag.packs.embeddedops.agents.firmware_agent:run_firmware_agent",
        "network_agent_rag.packs.embeddedops.agents.debug_agent:run_debug_agent",
    ),
    workflow_factories=(
        "network_agent_rag.packs.embeddedops.agents.workflow:create_embedded_workflow",
    ),
    permissions=(
        PermissionSpec(
            name=Permission.EMBEDDED_READ.value,
            description="Read embedded tasks, artifacts, and capabilities",
        ),
        PermissionSpec(
            name=Permission.EMBEDDED_GENERATE.value,
            description="Create embedded design/firmware tasks",
        ),
        PermissionSpec(
            name=Permission.EMBEDDED_SIMULATE.value,
            description="Approve simulation runs and execute virtual-hardware tools",
        ),
    ),
    roles=(
        RoleSpec(
            name="EmbeddedEngineer",
            permissions=(
                Permission.EMBEDDED_READ.value,
                Permission.EMBEDDED_GENERATE.value,
                Permission.EMBEDDED_SIMULATE.value,
            ),
            description="Full embedded lifecycle engineer role",
        ),
    ),
    knowledge_collections=(
        KnowledgeCollectionSpec(
            name="embedded_knowledge",
            directory="src/network_agent_rag/packs/embeddedops/knowledge_corpus",
        ),
    ),
    evaluation_cases=("src/network_agent_rag/packs/embeddedops/evaluation_cases",),
    api_routers=(
        RouterSpec(
            prefix="/embedded",
            factory="network_agent_rag.packs.embeddedops.api:create_embedded_router",
        ),
    ),
    ui_extensions=(
        UiExtensionSpec(view_id="embedded", label="Embedded Lab", glyph="⬡"),
    ),
)


def register_embeddedops_pack(registry: "network_agent_rag.packs.PackRegistry") -> DomainPackSpec:
    """Register the embeddedops pack through the full pipeline (fail-fast)."""

    registry.register(EMBEDDEDOPS_PACK)
    return EMBEDDEDOPS_PACK


__all__ = ["EMBEDDEDOPS_PACK", "register_embeddedops_pack"]
