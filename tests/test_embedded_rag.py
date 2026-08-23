"""Cross-collection search and embedded corpus ingestion tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from network_agent_rag.packs.embeddedops.knowledge import (
    build_embedded_vector_store,
    load_embedded_documents,
)
from network_agent_rag.rag.vector_store import create_vector_store, search_collections


class KeywordEmbeddings(Embeddings):
    """Deterministic keyword-count embedding for offline tests."""

    _KEYWORDS = ("esp32", "spi", "router", "vlan")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [float(lowered.count(keyword)) for keyword in self._KEYWORDS] + [1.0]


class SearchCollectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.persist = Path(self._tmp.name) / "chroma"
        self.embeddings = KeywordEmbeddings()
        self.embedded = create_vector_store(
            [
                Document(
                    page_content="ESP32 SPI 时钟模式配置错误会导致读取 0xFF",
                    metadata={"source": "spi-cases.md"},
                )
            ],
            persist_directory=self.persist,
            collection_name="embedded_knowledge",
            embeddings=self.embeddings,
        )
        self.network = create_vector_store(
            [
                Document(
                    page_content="路由器 router vlan trunk 配置诊断",
                    metadata={"source": "network.md"},
                )
            ],
            persist_directory=self.persist,
            collection_name="network_knowledge",
            embeddings=self.embeddings,
        )

    def tearDown(self) -> None:
        for store in (self.embedded, self.network):
            store._client.close()
        del self.embedded, self.network
        self._tmp.cleanup()

    def test_merges_and_ranks_across_collections(self) -> None:
        results = search_collections(
            "ESP32 SPI 故障", [self.embedded, self.network], k=4
        )
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("ESP32 SPI", results[0][0].page_content)

    def test_deduplicates_overlapping_documents(self) -> None:
        results = search_collections(
            "ESP32 SPI 故障", [self.embedded, self.embedded], k=4
        )
        contents = [document.page_content for document, _ in results]
        self.assertEqual(len(contents), len(set(contents)))

    def test_rejects_empty_inputs(self) -> None:
        with self.assertRaises(ValueError):
            search_collections("", [self.embedded])
        with self.assertRaises(ValueError):
            search_collections("query", [])

    def test_network_only_query_still_finds_network_collection(self) -> None:
        results = search_collections(
            "router vlan 诊断", [self.embedded, self.network], k=4
        )
        self.assertIn("network.md", str(results[0][0].metadata.get("source")))


class EmbeddedCorpusTests(unittest.TestCase):
    def test_loads_markdown_chunks_from_repository_corpus(self) -> None:
        documents = load_embedded_documents()
        self.assertGreater(len(documents), 0)
        self.assertTrue(
            all(
                isinstance(document, Document) for document in documents
            )
        )

    def test_missing_directory_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_embedded_documents("does/not/exist")

    def test_builds_vector_store_from_corpus(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = build_embedded_vector_store(
                persist_directory=Path(tmp) / "chroma",
                embeddings=KeywordEmbeddings(),
            )
            results = store.similarity_search_with_relevance_scores(
                "SPI 读取 0xFF", k=2
            )
            self.assertGreaterEqual(len(results), 1)
            store._client.close()
            del store


if __name__ == "__main__":
    unittest.main()
