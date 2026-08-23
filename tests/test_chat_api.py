"""Tests for the streaming chat and in-memory history API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import import_module
import importlib.util
import inspect
import json
import unittest
import warnings

from langchain_core.documents import Document
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient

from network_agent_rag.packs.networkops.agents import create_agent_workflow


def parse_sse(payload: str) -> list[tuple[str, dict[str, object]]]:
    normalized = payload.replace("\r\n", "\n").strip()
    events: list[tuple[str, dict[str, object]]] = []
    for block in normalized.split("\n\n"):
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


def build_workflow(*, invalid_answer: bool = False):
    return create_agent_workflow(
        analyze_query=lambda query: "general" if invalid_answer else "hybrid",
        retrieve_documents=lambda query: [
            Document(
                page_content="BGP troubleshooting guide",
                metadata={"source": "bgp.md", "section": "Neighbors"},
            )
        ],
        retrieve_topology=lambda query: {"path": ["edge-rtr-01", "core-sw-01"]},
        retrieve_metrics=lambda query: {"cpu_percent": 32.5},
        grade_documents=lambda state: {
            "relevance_score": 0.9,
            "feedback": "Relevant",
        },
        rewrite_query=lambda state: f"{state['rewritten_query']} refined",
        generate_answer=(
            (lambda state: " ")
            if invalid_answer
            else (lambda state: "Check the BGP neighbor and uplink metrics.")
        ),
        check_answer=lambda state: {"approved": True, "feedback": ""},
    )


class BrokenWorkflow:
    async def astream(self, *args, **kwargs):
        raise RuntimeError("secret backend failure")
        yield  # pragma: no cover


class StateWorkflow:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    async def astream(self, *args, **kwargs):
        yield {"QueryAnalyzer": self.state}


def final_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "user_query": "Question",
        "rewritten_query": "Question",
        "intent": "knowledge",
        "documents": [Document(page_content="Evidence", metadata={"source": "kb"})],
        "relevance_score": 0.9,
        "grading_feedback": "Relevant",
        "topology_context": {},
        "metrics": {},
        "answer": "Answer",
        "iteration": 1,
        "checker_feedback": None,
        "error": None,
    }
    state.update(overrides)
    return state


class ChatApiTests(unittest.TestCase):
    def _create_app(self, workflow=None):
        module = import_module("network_agent_rag.main")
        create_app = module.create_app
        parameters = inspect.signature(create_app).parameters
        self.assertIn("agent_workflow", parameters)
        self.assertIn("history_store", parameters)
        return create_app(agent_workflow=workflow)

    def _post_events(
        self,
        client: TestClient,
        *,
        session_id: str = "session-1",
        query: str = "Diagnose BGP packet loss",
    ) -> tuple[object, list[tuple[str, dict[str, object]]]]:
        response = client.post(
            "/api/v1/chat",
            json={"session_id": session_id, "query": query},
        )
        return response, parse_sse(response.text)

    def test_exports_pydantic_api_models(self) -> None:
        spec = importlib.util.find_spec("network_agent_rag.packs.networkops.api.schemas")
        self.assertIsNotNone(spec)
        if spec is None:
            return
        schemas = import_module("network_agent_rag.packs.networkops.api.schemas")
        for name in (
            "ChatRequest",
            "ChatResponse",
            "HistoryMessage",
            "HistoryResponse",
            "NodeProgress",
            "SourceDocument",
            "StreamError",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(schemas, name, None))

    def test_openapi_registers_chat_history_and_health(self) -> None:
        application = self._create_app(build_workflow())
        paths = application.openapi()["paths"]
        self.assertIn("/api/v1/health", paths)
        self.assertIn("/api/v1/chat", paths)
        self.assertIn("/api/v1/history", paths)
        self.assertIn("post", paths["/api/v1/chat"])
        self.assertIn("get", paths["/api/v1/history"])
        chat_content = paths["/api/v1/chat"]["post"]["responses"]["200"]["content"]
        self.assertIn("text/event-stream", chat_content)

    def test_chat_requires_an_injected_workflow(self) -> None:
        client = TestClient(self._create_app())
        response = client.post(
            "/api/v1/chat",
            json={"session_id": "session-1", "query": "Question"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Agent workflow is not configured")

    def test_request_models_reject_invalid_values_and_extra_fields(self) -> None:
        client = TestClient(self._create_app(build_workflow()))
        invalid_payloads = (
            {"session_id": " ", "query": "Question"},
            {"session_id": "bad/session", "query": "Question"},
            {"session_id": "x" * 129, "query": "Question"},
            {"session_id": "session-1", "query": " "},
            {"session_id": "session-1", "query": "x" * 10001},
            {"session_id": "session-1", "query": "Question", "extra": True},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = client.post("/api/v1/chat", json=payload)
                self.assertEqual(response.status_code, 422)

        response = client.get("/api/v1/history", params={"session_id": "bad/id"})
        self.assertEqual(response.status_code, 422)

    def test_chat_streams_node_progress_and_serialized_answer(self) -> None:
        client = TestClient(self._create_app(build_workflow()))
        response, events = self._post_events(client)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(events[0], ("start", {"session_id": "session-1"}))
        self.assertEqual(events[-1][0], "answer")

        node_names = [data["node"] for event, data in events if event == "node"]
        self.assertEqual(
            node_names,
            [
                "QueryAnalyzer",
                "Router",
                "Retrieval",
                "DocumentGrader",
                "Generator",
                "HallucinationChecker",
            ],
        )
        self.assertFalse(any(str(name).startswith("__") for name in node_names))

        answer = events[-1][1]
        self.assertEqual(answer["session_id"], "session-1")
        self.assertEqual(answer["intent"], "hybrid")
        self.assertEqual(answer["relevance_score"], 0.9)
        self.assertEqual(answer["topology_context"]["path"][0], "edge-rtr-01")
        self.assertEqual(answer["metrics"]["cpu_percent"], 32.5)
        self.assertEqual(answer["source_documents"][0]["metadata"]["source"], "bgp.md")
        self.assertIsNone(answer["error"])

    def test_history_is_ordered_and_isolated_by_session(self) -> None:
        client = TestClient(self._create_app(build_workflow()))
        empty = client.get(
            "/api/v1/history", params={"session_id": "unknown-session"}
        ).json()
        self.assertEqual(empty, {"session_id": "unknown-session", "messages": []})

        self._post_events(client, session_id="session-a", query="First question")
        self._post_events(client, session_id="session-a", query="Second question")
        self._post_events(client, session_id="session-b", query="Other question")

        history_a = client.get(
            "/api/v1/history", params={"session_id": "session-a"}
        ).json()
        self.assertEqual(
            [message["role"] for message in history_a["messages"]],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertEqual(history_a["messages"][0]["content"], "First question")
        self.assertEqual(history_a["messages"][2]["content"], "Second question")
        for message in history_a["messages"]:
            self.assertIsNotNone(datetime.fromisoformat(message["created_at"]).tzinfo)

        history_b = client.get(
            "/api/v1/history", params={"session_id": "session-b"}
        ).json()
        self.assertEqual(len(history_b["messages"]), 2)
        self.assertEqual(history_b["messages"][0]["content"], "Other question")

    def test_history_store_returns_message_copies(self) -> None:
        history_module = import_module("network_agent_rag.packs.networkops.api.history")
        store = history_module.InMemoryHistoryStore()
        store.append_exchange("session-1", "Original question", "Original answer")

        messages = store.get("session-1")
        messages[0].content = "Changed outside the store"

        self.assertEqual(store.get("session-1")[0].content, "Original question")

    def test_history_store_keeps_concurrent_exchanges_atomic(self) -> None:
        history_module = import_module("network_agent_rag.packs.networkops.api.history")
        store = history_module.InMemoryHistoryStore()
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(store.append_exchange, "shared", f"q-{i}", f"a-{i}")
                for i in range(20)
            ]
            for future in futures:
                future.result()

        messages = store.get("shared")
        self.assertEqual(len(messages), 40)
        for index in range(0, len(messages), 2):
            self.assertEqual(messages[index].role, "user")
            self.assertEqual(messages[index + 1].role, "assistant")
            self.assertEqual(
                messages[index].content.removeprefix("q-"),
                messages[index + 1].content.removeprefix("a-"),
            )

    def test_create_app_preserves_a_falsey_injected_history_store(self) -> None:
        main = import_module("network_agent_rag.main")
        history_module = import_module("network_agent_rag.packs.networkops.api.history")

        class FalseyStore(history_module.InMemoryHistoryStore):
            def __bool__(self) -> bool:
                return False

        store = FalseyStore()
        application = main.create_app(history_store=store)
        self.assertIs(application.state.history_store, store)

    def test_structured_graph_error_is_answered_and_saved(self) -> None:
        client = TestClient(self._create_app(build_workflow(invalid_answer=True)))
        _, events = self._post_events(client, session_id="failed-session")

        self.assertEqual(events[-1][0], "answer")
        self.assertIn("Generator", events[-1][1]["error"])
        self.assertTrue(events[-1][1]["answer"])
        node_names = [data["node"] for event, data in events if event == "node"]
        self.assertFalse(any(str(name).startswith("__") for name in node_names))

        history = client.get(
            "/api/v1/history", params={"session_id": "failed-session"}
        ).json()
        self.assertEqual(len(history["messages"]), 2)
        self.assertEqual(history["messages"][-1]["content"], events[-1][1]["answer"])

    def test_unexpected_graph_error_streams_generic_error_without_history(self) -> None:
        client = TestClient(self._create_app(BrokenWorkflow()))
        with self.assertLogs("network_agent_rag.packs.networkops.api.router", level="ERROR"):
            _, events = self._post_events(client, session_id="broken-session")

        self.assertEqual(
            [event for event, data in events],
            ["start", "error"],
        )
        self.assertEqual(events[-1][1]["code"], "AGENT_EXECUTION_FAILED")
        self.assertEqual(events[-1][1]["message"], "Agent execution failed.")
        self.assertNotIn("secret backend failure", json.dumps(events[-1][1]))

        history = client.get(
            "/api/v1/history", params={"session_id": "broken-session"}
        ).json()
        self.assertEqual(history["messages"], [])

    def test_answer_serialization_failure_does_not_write_history(self) -> None:
        state = final_state(
            documents=[
                Document(page_content="Evidence", metadata={"bad": object()})
            ]
        )
        client = TestClient(self._create_app(StateWorkflow(state)))
        with self.assertLogs("network_agent_rag.packs.networkops.api.router", level="ERROR"):
            _, events = self._post_events(client, session_id="serialization-failure")

        self.assertEqual(events[-1][0], "error")
        history = client.get(
            "/api/v1/history",
            params={"session_id": "serialization-failure"},
        ).json()
        self.assertEqual(history["messages"], [])

    def test_response_models_do_not_coerce_invalid_state_types(self) -> None:
        client = TestClient(
            self._create_app(StateWorkflow(final_state(iteration="1")))
        )
        with self.assertLogs("network_agent_rag.packs.networkops.api.router", level="ERROR"):
            _, events = self._post_events(client, session_id="strict-state")

        self.assertEqual(events[-1][0], "error")
        history = client.get(
            "/api/v1/history", params={"session_id": "strict-state"}
        ).json()
        self.assertEqual(history["messages"], [])


if __name__ == "__main__":
    unittest.main()
