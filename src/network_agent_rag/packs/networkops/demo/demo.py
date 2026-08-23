"""Explicit assembly for the deterministic SW1-SW2 diagnosis demonstration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma
from langgraph.graph.state import CompiledStateGraph

from network_agent_rag.packs.networkops.agents import (
    DiagnosisPlan,
    create_network_diagnosis_workflow,
    query_interface,
    query_logs,
)
from network_agent_rag.packs.networkops.domain.topology import NetworkTopology
from network_agent_rag.main import create_app
from network_agent_rag.rag import create_vector_store, load_documents, search


_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_PACK_ROOT = Path(__file__).resolve().parents[1]
_KNOWLEDGE_PATH = (
    _PACK_ROOT / "knowledge_corpus" / "optical_module_degradation.md"
)
_TOPOLOGY_PATH = _PROJECT_ROOT / "topology" / "sw1_sw2.json"


def create_demo_workflow(
    *,
    persist_directory: str | Path = "data/chroma/sw1_sw2_demo",
    embeddings: Embeddings | None = None,
) -> CompiledStateGraph:
    """Build the explicit demo graph; default embeddings load local BGE-M3."""

    workflow, _ = _assemble_demo(
        persist_directory=persist_directory,
        embeddings=embeddings,
    )
    return workflow


def _assemble_demo(
    *,
    persist_directory: str | Path,
    embeddings: Embeddings | None,
) -> tuple[CompiledStateGraph, Chroma]:
    chunks = load_documents(_KNOWLEDGE_PATH)
    for chunk in chunks:
        chunk.metadata["evidence_ref"] = "KB-4001"
    vector_store = create_vector_store(
        chunks,
        persist_directory=persist_directory,
        collection_name="sw1_sw2_diagnosis",
        embeddings=embeddings,
    )
    topology = NetworkTopology.from_json(_TOPOLOGY_PATH)

    def analyze_query(query: str) -> DiagnosisPlan:
        return {
            "devices": ["SW1", "SW2"],
            "interfaces": ["SW1:Gi0/1", "SW2:Gi0/24"],
            "symptom": "链路丢包",
            "start_time": "2026-07-12T08:30:00+08:00",
            "end_time": "2026-07-12T09:00:00+08:00",
            "required_sources": [
                "topology",
                "monitoring",
                "logs",
                "knowledge",
            ],
        }

    def retrieve_topology(plan: DiagnosisPlan) -> dict[str, object]:
        return topology.query_path_details(
            plan["devices"][0],
            plan["devices"][-1],
            relation="connect",
        )

    def retrieve_metrics(
        plan: DiagnosisPlan,
        topology_context: dict[str, object],
    ) -> dict[str, object]:
        interfaces: list[dict[str, object]] = []
        for reference in plan["interfaces"]:
            device_id, interface_name = reference.split(":", 1)
            result = query_interface.invoke(
                {"device_id": device_id, "interface_name": interface_name}
            )
            if not result["ok"]:
                raise ValueError(result["message"])
            interface = dict(result["interfaces"][0])
            interface["device_id"] = result["device_id"]
            interface["evidence_ref"] = f"METRIC-{3001 + len(interfaces)}"
            interfaces.append(interface)
        return {
            "status": "degraded",
            "packet_loss_percent": max(
                float(interface["packet_loss_percent"])
                for interface in interfaces
            ),
            "interfaces": interfaces,
            "observed_at": "2026-07-12T09:00:00+08:00",
        }

    def retrieve_log_records(plan: DiagnosisPlan) -> dict[str, object]:
        return query_logs.invoke(
            {
                "source_ids": plan["devices"],
                "start": plan["start_time"],
                "end": plan["end_time"],
                "query": "crc optical",
                "limit": 20,
            }
        )

    def retrieve_knowledge(query: str) -> list[Document]:
        results = search(query, vector_store, k=min(4, len(chunks)))
        documents: list[Document] = []
        for document, score in results:
            metadata = dict(document.metadata)
            metadata["relevance_score"] = float(score)
            documents.append(
                Document(page_content=document.page_content, metadata=metadata)
            )
        return documents

    workflow = create_network_diagnosis_workflow(
        analyze_query=analyze_query,
        retrieve_topology=retrieve_topology,
        retrieve_metrics=retrieve_metrics,
        retrieve_logs=retrieve_log_records,
        retrieve_documents=retrieve_knowledge,
    )
    return workflow, vector_store


def create_demo_app(
    *,
    persist_directory: str | Path = "data/chroma/sw1_sw2_demo",
    embeddings: Embeddings | None = None,
) -> FastAPI:
    """Create FastAPI with the explicit diagnosis graph injected."""

    workflow, vector_store = _assemble_demo(
        persist_directory=persist_directory,
        embeddings=embeddings,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            # LangChain Chroma currently exposes no public close method. Keep
            # the compatibility call isolated to this demo resource boundary.
            vector_store._client.close()

    return create_app(agent_workflow=workflow, lifespan=lifespan)


__all__ = ["create_demo_app", "create_demo_workflow"]
