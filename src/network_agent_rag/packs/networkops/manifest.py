"""NetworkOps DomainPack manifest: the single source of truth for pack assets.

Notes:
- capabilities stay empty in v0.16: network tools remain dependency-injected
  callables (explicit debt recorded in RFC sec 11 -- promoting them to
  capabilities requires workflow-assembly redesign, out of step-3 scope).
- api_routers lists the routers the PLATFORM mounts (the chat surface).
  The enterprise/console routers are mounted by the pack-internal
  ``create_enterprise_app`` assembly, not by the platform loop.
"""

from __future__ import annotations

from network_agent_rag.auth import Permission
from network_agent_rag.packs.models import (
    DomainPackSpec,
    KnowledgeCollectionSpec,
    LifecycleSpec,
    PermissionSpec,
    RoleSpec,
    RouterSpec,
)


NETWORKOPS_PACK = DomainPackSpec(
    name="networkops",
    version="0.16.0",
    description=(
        "NetworkOps domain pack: multi-agent network diagnosis, enterprise "
        "workflow with approval-gated execution, digital twin, and the "
        "network operator console/chat surfaces."
    ),
    lifecycle=LifecycleSpec(),
    capabilities=(),
    agent_factories=(
        "network_agent_rag.packs.networkops.agents.multi_agent.topology_agent:run_topology_agent",
        "network_agent_rag.packs.networkops.agents.multi_agent.log_agent:run_log_agent",
        "network_agent_rag.packs.networkops.agents.multi_agent.diagnosis_agent:run_diagnosis_agent",
        "network_agent_rag.packs.networkops.agents.multi_agent.repair_agent:run_repair_agent",
        "network_agent_rag.packs.networkops.agents.multi_agent.report_agent:run_report_agent",
    ),
    workflow_factories=(
        "network_agent_rag.packs.networkops.agents.multi_agent.workflow:create_multi_agent_workflow",
        "network_agent_rag.packs.networkops.agents.enterprise.workflow:create_enterprise_workflow",
        "network_agent_rag.packs.networkops.agents.workflow:create_agent_workflow",
    ),
    permissions=(
        PermissionSpec(
            name=Permission.VIEW_INCIDENT.value,
            description="View network incidents and their traces",
        ),
        PermissionSpec(
            name=Permission.VIEW_TRACE.value,
            description="View observability traces",
        ),
        PermissionSpec(
            name=Permission.CREATE_REPAIR_PLAN.value,
            description="Create network repair plans",
        ),
        PermissionSpec(
            name=Permission.EXECUTE_REPAIR.value,
            description="Execute allowlisted network repair actions",
        ),
        PermissionSpec(
            name=Permission.APPROVE_REPAIR.value,
            description="Approve high-risk network repairs",
        ),
    ),
    roles=(
        RoleSpec(
            name="Operator",
            permissions=(Permission.VIEW_INCIDENT.value, Permission.VIEW_TRACE.value),
            description="Read-only network operations role",
        ),
        RoleSpec(
            name="Engineer",
            permissions=(
                Permission.VIEW_INCIDENT.value,
                Permission.VIEW_TRACE.value,
                Permission.CREATE_REPAIR_PLAN.value,
                Permission.EXECUTE_REPAIR.value,
            ),
            description="Network engineer role",
        ),
        RoleSpec(
            name="Admin",
            permissions=(
                Permission.VIEW_INCIDENT.value,
                Permission.VIEW_TRACE.value,
                Permission.APPROVE_REPAIR.value,
                Permission.MANAGE_SYSTEM.value,
            ),
            description="Network administrator (MANAGE_SYSTEM is platform-provided)",
        ),
    ),
    knowledge_collections=(
        KnowledgeCollectionSpec(
            name="network_knowledge",
            directory="src/network_agent_rag/packs/networkops/knowledge_corpus",
        ),
    ),
    evaluation_cases=("src/network_agent_rag/packs/networkops/evaluation",),
    api_routers=(
        RouterSpec(
            prefix="",
            factory="network_agent_rag.packs.networkops.api.router:api_router",
        ),
    ),
)


def register_networkops_pack(
    registry: "network_agent_rag.packs.PackRegistry",
    *,
    knowledge_persist_directory: str | None = None,
    knowledge_embeddings=None,
) -> DomainPackSpec:
    """Register the networkops pack through the full pipeline (fail-fast).

    Knowledge initialization mirrors embeddedops: skip-if-present, build
    from the pack corpus when absent, fail loudly on error; no directory
    means the knowledge step is a no-op (offline unit tests).
    """

    registry.register(NETWORKOPS_PACK)
    if knowledge_persist_directory is not None:
        from pathlib import Path as _Path

        from network_agent_rag.packs.embeddedops.knowledge import (
            initialize_collection,
        )

        for collection in NETWORKOPS_PACK.knowledge_collections:
            initialize_collection(
                collection_name=collection.name,
                corpus_directory=_Path(collection.directory),
                persist_directory=knowledge_persist_directory,
                embeddings=knowledge_embeddings,
            )
    return NETWORKOPS_PACK


__all__ = ["NETWORKOPS_PACK", "register_networkops_pack"]
