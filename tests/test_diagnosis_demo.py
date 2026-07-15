"""Integration tests for the explicit SW1-SW2 demonstration assembly."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import json
import tempfile
import unittest
import warnings

from langchain_core.embeddings import Embeddings
from starlette.testclient import TestClient


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float("光模块" in text)] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0]


def parse_sse(payload: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in payload.replace("\r\n", "\n").strip().split("\n\n"):
        lines = block.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


class DiagnosisDemoTests(unittest.TestCase):
    def test_sample_knowledge_describes_the_optical_degradation_symptoms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        knowledge = root / "knowledge" / "diagnosis" / "optical_module_degradation.md"
        text = knowledge.read_text(encoding="utf-8")
        self.assertIn("CRC", text)
        self.assertIn("接收光功率", text)
        self.assertIn("光模块", text)
        self.assertIn("人工批准", text)

    def test_demo_uses_chroma_and_streams_all_diagnosis_nodes(self) -> None:
        demo = import_module("network_agent_rag.demo")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            with tempfile.TemporaryDirectory() as directory:
                app = demo.create_demo_app(
                    persist_directory=directory,
                    embeddings=FakeEmbeddings(),
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "session_id": "sw1-sw2-demo",
                            "query": "SW1 到 SW2 链路丢包，请结合拓扑、日志和知识库分析",
                        },
                    )

        events = parse_sse(response.text)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [data["node"] for event, data in events if event == "node"],
            [
                "QueryAnalyzer",
                "TopologyTool",
                "MonitorTool",
                "LogTool",
                "RAG",
                "EvidenceCorrelator",
                "Generator",
                "HallucinationChecker",
            ],
        )
        answer = events[-1][1]
        self.assertEqual(events[-1][0], "answer")
        self.assertIn("规则诊断置信度 80%", answer["answer"])
        self.assertEqual(answer["topology_context"]["path"], ["SW1", "SW2"])
        self.assertEqual(len(answer["metrics"]["interfaces"]), 2)
        self.assertTrue(answer["source_documents"])
        self.assertIn("光模块", answer["source_documents"][0]["page_content"])

    def test_default_module_application_remains_unconfigured(self) -> None:
        main = import_module("network_agent_rag.main")
        self.assertIsNone(main.app.state.agent_workflow)


if __name__ == "__main__":
    unittest.main()
