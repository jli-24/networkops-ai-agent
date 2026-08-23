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


def register_embeddedops_pack(
    registry: "network_agent_rag.packs.PackRegistry",
    *,
    knowledge_persist_directory: str | None = None,
    knowledge_embeddings=None,
) -> DomainPackSpec:
    """Register the embeddedops pack through the full pipeline (fail-fast).

    When ``knowledge_persist_directory`` is provided, each declared
    knowledge collection is initialized at registration time (skip when
    already present, build from the pack corpus when absent, fail loudly
    on error). Without it the knowledge step is a no-op so unit tests
    stay offline; deployment assembly passes the production directory.
    """

    registry.register(EMBEDDEDOPS_PACK)
    if knowledge_persist_directory is not None:
        from pathlib import Path as _Path

        from network_agent_rag.packs.embeddedops.knowledge import (
            initialize_collection,
        )

        for collection in EMBEDDEDOPS_PACK.knowledge_collections:
            initialize_collection(
                collection_name=collection.name,
                corpus_directory=_Path(collection.directory),
                persist_directory=knowledge_persist_directory,
                embeddings=knowledge_embeddings,
            )
    return EMBEDDEDOPS_PACK


__all__ = ["EMBEDDEDOPS_PACK", "register_embeddedops_pack"]
