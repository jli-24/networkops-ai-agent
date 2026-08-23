"""EmbeddedOps knowledge corpus loading and ingestion (pack-owned corpus)."""

from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from network_agent_rag.core.config import Settings
from network_agent_rag.rag.documents import load_documents
from network_agent_rag.rag.vector_store import create_vector_store

CORPUS_DIRECTORY = Path(__file__).resolve().parent / "knowledge_corpus"


def load_embedded_documents(
    directory: str | Path | None = None,
) -> list[Document]:
    """Load chunked documents from the pack-owned knowledge corpus."""

    source = Path(directory) if directory else CORPUS_DIRECTORY
    if not source.exists():
        raise FileNotFoundError(f"embedded knowledge directory not found: {source}")
    return load_documents(source)


def build_embedded_vector_store(
    *,
    persist_directory: str | Path = "data/chroma",
    embeddings: Embeddings | None = None,
    settings: Settings | None = None,
) -> Chroma:
    """Rebuild the persisted embedded-knowledge collection."""

    settings = settings or Settings()
    documents = load_embedded_documents()
    if not documents:
        raise ValueError("embedded knowledge directory contains no documents")
    return create_vector_store(
        documents,
        persist_directory=persist_directory,
        collection_name=settings.embedded_collection_name,
        embeddings=embeddings,
    )


def initialize_collection(
    *,
    collection_name: str,
    corpus_directory: str | Path,
    persist_directory: str | Path,
    embeddings: Embeddings | None = None,
) -> str:
    """Initialize a knowledge collection from a pack corpus if absent.

    Returns ``"built"`` or ``"skipped"`` (collection already non-empty);
    any failure raises -- registration is fail-loud, never silently
    degraded (charter iron law 25: executable evidence, no audit-only
    conclusions).
    """

    if not collection_name.strip():
        raise ValueError("collection_name must not be empty")
    directory = Path(persist_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    probe = Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=str(directory),
    )
    try:
        existing = probe.get()
        if existing and existing.get("ids"):
            return "skipped"
    finally:
        probe._client.close()
    corpus_path = Path(corpus_directory)
    if not corpus_path.is_dir():
        raise FileNotFoundError(f"knowledge corpus not found: {corpus_path}")
    documents = load_documents(corpus_path)
    if not documents:
        raise ValueError(f"knowledge corpus contains no documents: {corpus_path}")
    store = create_vector_store(
        documents,
        persist_directory=directory,
        collection_name=collection_name,
        embeddings=embeddings,
    )
    store._client.close()
    return "built"


__all__ = [
    "CORPUS_DIRECTORY",
    "build_embedded_vector_store",
    "initialize_collection",
    "load_embedded_documents",
]
