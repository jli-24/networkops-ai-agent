"""Embedding, Chroma persistence, and semantic search."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"


@lru_cache(maxsize=1)
def _default_embeddings() -> Embeddings:
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=_DEFAULT_EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )


def create_vector_store(
    documents: Sequence[Document],
    *,
    persist_directory: str | Path = "data/chroma",
    collection_name: str = "network_knowledge",
    embeddings: Embeddings | None = None,
) -> Chroma:
    """Rebuild and persist a Chroma collection from document chunks."""

    chunks = list(documents)
    if not chunks:
        raise ValueError("documents must not be empty")
    if not collection_name.strip():
        raise ValueError("collection_name must not be empty")

    directory = Path(persist_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    vector_store = Chroma(
        collection_name=collection_name,
        embedding_function=embeddings or _default_embeddings(),
        persist_directory=str(directory),
        collection_configuration={"hnsw": {"space": "cosine"}},
    )
    vector_store.reset_collection()
    vector_store.add_documents(chunks)
    return vector_store


def search(
    query: str,
    vector_store: Chroma,
    *,
    k: int = 4,
) -> list[tuple[Document, float]]:
    """Search a Chroma collection and return documents with relevance scores."""

    if not query.strip():
        raise ValueError("query must not be empty")
    if k <= 0:
        raise ValueError("k must be greater than zero")
    return vector_store.similarity_search_with_relevance_scores(query, k=k)


def search_collections(
    query: str,
    vector_stores: Sequence[Chroma],
    *,
    k: int = 4,
) -> list[tuple[Document, float]]:
    """Search multiple collections (e.g. embedded + network) and merge.

    Cross-domain questions like an ESP32 Wi-Fi failure need both the embedded
    knowledge base and the network knowledge base. Results are merged,
    de-duplicated by document content and source, and truncated to ``k`` by
    relevance score.
    """

    if not query.strip():
        raise ValueError("query must not be empty")
    if k <= 0:
        raise ValueError("k must be greater than zero")
    stores = list(vector_stores)
    if not stores:
        raise ValueError("vector_stores must not be empty")

    merged: dict[tuple[str, str], tuple[Document, float]] = {}
    for store in stores:
        for document, score in store.similarity_search_with_relevance_scores(
            query, k=k
        ):
            dedupe_key = (
                document.page_content,
                str(document.metadata.get("source", "")),
            )
            existing = merged.get(dedupe_key)
            if existing is None or score > existing[1]:
                merged[dedupe_key] = (document, score)
    ranked = sorted(merged.values(), key=lambda item: item[1], reverse=True)
    return ranked[:k]
