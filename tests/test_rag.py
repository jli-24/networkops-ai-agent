"""Tests for the network knowledge-base RAG module."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class DeterministicEmbeddings(Embeddings):
    """Small local embedding implementation for vector-store tests."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        lowered = text.casefold()
        return [
            float(lowered.count("router")),
            float(lowered.count("vlan")),
            1.0,
        ]


class DocumentLoadingTests(unittest.TestCase):
    def test_markdown_preserves_title_path_and_fenced_cli_block(self) -> None:
        rag = import_module("network_agent_rag.rag")
        load_documents = getattr(rag, "load_documents", None)
        self.assertIsNotNone(load_documents)
        if load_documents is None:
            return

        cli_block = "```console\nRouter(config)# " + "description uplink " * 8 + "\n```"
        markdown = (
            "# Routing\n\n"
            "## BGP\n\n"
            "Use the following configuration on the edge router.\n\n"
            f"{cli_block}\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routing.md")
            path.write_text(markdown, encoding="utf-8")
            chunks = load_documents(path, chunk_size=80, chunk_overlap=10)

        cli_chunks = [
            chunk for chunk in chunks if chunk.metadata["content_type"] == "cli"
        ]
        self.assertEqual(len(cli_chunks), 1)
        self.assertIn(cli_block, cli_chunks[0].page_content)
        self.assertEqual(cli_chunks[0].metadata["title_path"], "Routing > BGP")
        self.assertEqual(cli_chunks[0].metadata["section_title"], "BGP")

    def test_markdown_recognizes_setext_headings(self) -> None:
        rag = import_module("network_agent_rag.rag")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "switching.md")
            path.write_text(
                "Switching\n=========\n\nVLAN\n----\n\nConfigure access ports.\n",
                encoding="utf-8",
            )
            chunks = rag.load_documents(path)

        self.assertEqual(chunks[0].metadata["title_path"], "Switching > VLAN")
        self.assertTrue(chunks[0].page_content.startswith("# Switching\n## VLAN"))

    def test_regular_text_uses_recursive_character_chunk_size(self) -> None:
        rag = import_module("network_agent_rag.rag")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routing.md")
            path.write_text("# Routing\n\n" + "router protocol " * 30, encoding="utf-8")
            chunks = rag.load_documents(path, chunk_size=60, chunk_overlap=10)

        text_chunks = [
            chunk for chunk in chunks if chunk.metadata["content_type"] == "text"
        ]
        bodies = [chunk.page_content.split("\n\n", 1)[1] for chunk in text_chunks]
        self.assertGreater(len(bodies), 1)
        self.assertTrue(all(len(body) <= 60 for body in bodies))
        self.assertTrue(all(chunk.metadata["title_path"] == "Routing" for chunk in text_chunks))

    def test_txt_decodes_gb18030_and_preserves_numbered_cli_section(self) -> None:
        rag = import_module("network_agent_rag.rag")
        text = (
            "1 网络基础\n\n"
            "介绍企业网络。\n\n"
            "1.1 OSPF配置\n\n"
            "Router(config)# router ospf 1\n"
            "Router(config-router)# network 10.0.0.0 0.0.0.255 area 0\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "network.txt")
            path.write_bytes(text.encode("gb18030"))
            chunks = rag.load_documents(path)

        cli_chunks = [
            chunk for chunk in chunks if chunk.metadata["content_type"] == "cli"
        ]
        self.assertEqual(len(cli_chunks), 1)
        self.assertEqual(
            cli_chunks[0].metadata["title_path"], "网络基础 > OSPF配置"
        )
        self.assertIn(
            "Router(config)# router ospf 1\n"
            "Router(config-router)# network 10.0.0.0 0.0.0.255 area 0",
            cli_chunks[0].page_content,
        )

    def test_directory_discovery_is_recursive_deduplicated_and_sorted(self) -> None:
        rag = import_module("network_agent_rag.rag")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            first = root / "a.md"
            second = nested / "b.txt"
            first.write_text("# A\n\nAlpha", encoding="utf-8")
            second.write_text("# B\n\nBeta", encoding="utf-8")
            (root / "ignored.json").write_text("{}", encoding="utf-8")

            chunks = rag.load_documents([root, first])

        sources = [chunk.metadata["source"] for chunk in chunks]
        self.assertEqual(sources, sorted({str(first.resolve()), str(second.resolve())}))

    def test_loader_validates_sources_and_chunk_parameters(self) -> None:
        rag = import_module("network_agent_rag.rag")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsupported = root / "data.json"
            unsupported.write_text("{}", encoding="utf-8")
            text = root / "data.txt"
            text.write_text("content", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                rag.load_documents(root / "missing.txt")
            with self.assertRaises(ValueError):
                rag.load_documents(unsupported)
            with self.assertRaises(ValueError):
                rag.load_documents(text, chunk_size=0)
            with self.assertRaises(ValueError):
                rag.load_documents(text, chunk_size=10, chunk_overlap=10)

    def test_pdf_loads_text_with_page_metadata(self) -> None:
        import pymupdf

        rag = import_module("network_agent_rag.rag")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routing.pdf")
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 72), "Routing Guide", fontsize=20)
            page.insert_text((72, 110), "Configure BGP neighbors on the edge router.")
            pdf.save(path)
            pdf.close()

            chunks = rag.load_documents(path)

        self.assertTrue(chunks)
        self.assertTrue(any("Routing Guide" in chunk.page_content for chunk in chunks))
        self.assertIn("Routing Guide", chunks[0].metadata["title_path"])
        self.assertEqual(chunks[0].metadata["page"], 1)
        self.assertEqual(chunks[0].metadata["file_type"], "pdf")

    def test_pdf_rejects_pages_without_extractable_text(self) -> None:
        import pymupdf

        rag = import_module("network_agent_rag.rag")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "scanned.pdf")
            pdf = pymupdf.open()
            pdf.new_page()
            pdf.save(path)
            pdf.close()

            with self.assertRaisesRegex(ValueError, "scanned|OCR|extractable"):
                rag.load_documents(path)

    def test_pdf_carries_heading_path_across_page_boundaries(self) -> None:
        import pymupdf

        rag = import_module("network_agent_rag.rag")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "routing.pdf")
            pdf = pymupdf.open()
            first_page = pdf.new_page()
            first_page.insert_text((72, 72), "Routing Guide", fontsize=20)
            first_page.insert_text((72, 110), "BGP overview on the first page.")
            second_page = pdf.new_page()
            second_page.insert_text((72, 72), "BGP continuation on the second page.")
            pdf.save(path)
            pdf.close()

            chunks = rag.load_documents(path)

        second_page_chunks = [chunk for chunk in chunks if chunk.metadata["page"] == 2]
        self.assertTrue(second_page_chunks)
        self.assertTrue(
            all("Routing Guide" in chunk.metadata["title_path"] for chunk in second_page_chunks)
        )


class VectorStoreTests(unittest.TestCase):
    def test_default_embeddings_use_normalized_bge_m3_and_are_cached(self) -> None:
        module = import_module("network_agent_rag.rag.vector_store")
        module._default_embeddings.cache_clear()
        sentinel = DeterministicEmbeddings()

        with patch(
            "langchain_huggingface.HuggingFaceEmbeddings", return_value=sentinel
        ) as constructor:
            first = module._default_embeddings()
            second = module._default_embeddings()

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)
        constructor.assert_called_once_with(
            model_name="BAAI/bge-m3",
            encode_kwargs={"normalize_embeddings": True},
        )

    def test_create_and_search_vector_store_with_scores(self) -> None:
        rag = import_module("network_agent_rag.rag")
        create_vector_store = getattr(rag, "create_vector_store", None)
        search = getattr(rag, "search", None)
        self.assertIsNotNone(create_vector_store)
        self.assertIsNotNone(search)
        if create_vector_store is None or search is None:
            return

        documents = [
            Document(page_content="router bgp configuration", metadata={"source": "a"}),
            Document(page_content="vlan access configuration", metadata={"source": "b"}),
        ]
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="legacy embedding function config", category=DeprecationWarning
            )
            with tempfile.TemporaryDirectory() as directory:
                store = create_vector_store(
                    documents,
                    persist_directory=directory,
                    collection_name="network_test",
                    embeddings=DeterministicEmbeddings(),
                )
                results = search("router", store, k=1)
                count = store._collection.count()
                store._client.close()
                del store

        self.assertEqual(count, 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].metadata["source"], "a")
        self.assertIsInstance(results[0][1], float)

    def test_create_vector_store_rebuilds_existing_collection(self) -> None:
        rag = import_module("network_agent_rag.rag")
        embeddings = DeterministicEmbeddings()

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="legacy embedding function config", category=DeprecationWarning
            )
            with tempfile.TemporaryDirectory() as directory:
                first = rag.create_vector_store(
                    [Document(page_content="router old", metadata={"source": "old"})],
                    persist_directory=directory,
                    collection_name="network_rebuild",
                    embeddings=embeddings,
                )
                first._client.close()
                del first

                second = rag.create_vector_store(
                    [Document(page_content="vlan new", metadata={"source": "new"})],
                    persist_directory=directory,
                    collection_name="network_rebuild",
                    embeddings=embeddings,
                )
                records = second.get(include=["metadatas"])
                second._client.close()
                del second

        self.assertEqual(len(records["ids"]), 1)
        self.assertEqual(records["metadatas"][0]["source"], "new")

    def test_vector_store_and_search_validate_inputs(self) -> None:
        rag = import_module("network_agent_rag.rag")

        with self.assertRaises(ValueError):
            rag.create_vector_store([], embeddings=DeterministicEmbeddings())
        with self.assertRaises(ValueError):
            rag.create_vector_store(
                [Document(page_content="content")],
                collection_name=" ",
                embeddings=DeterministicEmbeddings(),
            )
        with self.assertRaises(ValueError):
            rag.search(" ", object())
        with self.assertRaises(ValueError):
            rag.search("router", object(), k=0)


if __name__ == "__main__":
    unittest.main()
