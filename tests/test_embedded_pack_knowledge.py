"""Registration-time knowledge initialization (v0.16 acceptance #9).

Fresh-init: zero-state persist directory -> register -> retrieval hits.
Idempotency: re-registration skips, document count unchanged.
Fail-loud: broken corpus errors instead of degrading silently.
"""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from network_agent_rag.packs import PackRegistry
from network_agent_rag.packs.embeddedops.knowledge import initialize_collection
from network_agent_rag.packs.embeddedops.manifest import (
    EMBEDDEDOPS_PACK,
    register_embeddedops_pack,
)


class KeywordEmbeddings(Embeddings):
    """Deterministic offline embeddings matching corpus vocabulary."""

    _KEYWORDS = ("esp32", "spi", "i2c", "mcu", "freertos", "wifi")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [float(lowered.count(keyword)) for keyword in self._KEYWORDS] + [1.0]


def _register_with_knowledge(persist: Path):
    pack_registry = PackRegistry()
    return register_embeddedops_pack(
        pack_registry,
        knowledge_persist_directory=str(persist),
        knowledge_embeddings=KeywordEmbeddings(),
    )


class FreshInitTests(unittest.TestCase):
    def test_zero_state_registration_builds_and_retrieval_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / "chroma"
            _register_with_knowledge(persist)
            store = Chroma(
                collection_name="embedded_knowledge",
                embedding_function=KeywordEmbeddings(),
                persist_directory=str(persist),
            )
            results = store.similarity_search_with_relevance_scores(
                "SPI 通信失败 返回 0xFF", k=3
            )
            store._client.close()
            self.assertGreaterEqual(len(results), 1)
            joined = " ".join(document.page_content for document, _ in results)
            self.assertIn("SPI", joined)

    def test_registration_without_directory_is_offline_noop(self) -> None:
        pack_registry = PackRegistry()
        register_embeddedops_pack(pack_registry)
        self.assertEqual(len(pack_registry.list()), 1)


class IdempotencyTests(unittest.TestCase):
    def test_second_registration_skips_and_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / "chroma"
            _register_with_knowledge(persist)
            first = Chroma(
                collection_name="embedded_knowledge",
                embedding_function=KeywordEmbeddings(),
                persist_directory=str(persist),
            )
            count_before = len(first.get()["ids"])
            first._client.close()

            _register_with_knowledge(persist)

            second = Chroma(
                collection_name="embedded_knowledge",
                embedding_function=KeywordEmbeddings(),
                persist_directory=str(persist),
            )
            count_after = len(second.get()["ids"])
            second._client.close()
            self.assertEqual(count_after, count_before)
            self.assertGreater(count_before, 0)


class FailLoudTests(unittest.TestCase):
    def test_missing_corpus_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                initialize_collection(
                    collection_name="embedded_knowledge",
                    corpus_directory=Path(tmp) / "does-not-exist",
                    persist_directory=Path(tmp) / "chroma",
                    embeddings=KeywordEmbeddings(),
                )

    def test_empty_corpus_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty-corpus"
            empty.mkdir()
            with self.assertRaises(ValueError):
                initialize_collection(
                    collection_name="embedded_knowledge",
                    corpus_directory=empty,
                    persist_directory=Path(tmp) / "chroma",
                    embeddings=KeywordEmbeddings(),
                )

    def test_manifest_declares_knowledge_collection(self) -> None:
        self.assertEqual(
            [item.name for item in EMBEDDEDOPS_PACK.knowledge_collections],
            ["embedded_knowledge"],
        )


if __name__ == "__main__":
    unittest.main()
