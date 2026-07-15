"""Integration tests for the explicit v0.2 multi-agent demonstration."""

from __future__ import annotations

from importlib import import_module
import json
import tempfile
import unittest
import warnings

from langchain_core.embeddings import Embeddings
from starlette.exceptions import StarletteDeprecationWarning
from starlette.testclient import TestClient


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)


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


class MultiAgentDemoTests(unittest.TestCase):
    def test_demo_streams_supervisor_handoffs_and_api_compatible_answer(self) -> None:
        demo = import_module("network_agent_rag.multi_agent_demo")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            with tempfile.TemporaryDirectory() as directory:
                app = demo.create_multi_agent_demo_app(
                    persist_directory=directory,
                    embeddings=FakeEmbeddings(),
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "session_id": "multi-agent-demo",
                            "query": "SW1 到 SW2 链路丢包，请给出诊断和修复计划",
                        },
                    )

        events = parse_sse(response.text)
        nodes = [data["node"] for event, data in events if event == "node"]
        self.assertEqual(
            nodes,
            [
                "Supervisor",
                "TopologyAgent",
                "Supervisor",
                "LogAgent",
                "Supervisor",
                "DiagnosisAgent",
                "Supervisor",
                "RepairAgent",
                "Supervisor",
                "ReportAgent",
                "Supervisor",
            ],
        )
        answer = events[-1][1]
        self.assertEqual(events[-1][0], "answer")
        self.assertIn("规则诊断置信度 80%", answer["answer"])
        self.assertIn("未执行", answer["answer"])
        self.assertEqual(answer["topology_context"]["path"], ["SW1", "SW2"])
        self.assertEqual(len(answer["metrics"]["interfaces"]), 2)
        self.assertTrue(answer["source_documents"])
        self.assertIsNone(answer["error"])


if __name__ == "__main__":
    unittest.main()
