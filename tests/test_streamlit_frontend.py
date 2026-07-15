"""Tests for the Streamlit FastAPI client and presentation helpers."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from streamlit.testing.v1 import AppTest

from network_agent_rag.frontend.client import (
    FastAPIClient,
    FrontendAPIError,
    SSEEvent,
    _parse_sse,
)
from network_agent_rag.frontend.app import NODE_LABELS


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self):
        return iter(self._body.splitlines(keepends=True))

    def read(self) -> bytes:
        return self._body


class SSEParserTests(TestCase):
    def test_parses_crlf_multiline_data_and_final_unterminated_event(self) -> None:
        lines = [
            b"event: start\r\n",
            b'data: {"session_id":"demo"}\r\n',
            b"\r\n",
            b"event: node\n",
            b'data: {"node":\n',
            b'data: "Router"}\n',
            b"\n",
            b"event: answer\n",
            b'data: {"answer":"done"}',
        ]

        self.assertEqual(
            list(_parse_sse(lines)),
            [
                SSEEvent("start", {"session_id": "demo"}),
                SSEEvent("node", {"node": "Router"}),
                SSEEvent("answer", {"answer": "done"}),
            ],
        )

    def test_rejects_invalid_or_missing_sse_json(self) -> None:
        invalid_streams = (
            [b"event: answer\n", b"data: not-json\n", b"\n"],
            [b"event: answer\n", b"\n"],
            [b"data: []\n", b"\n"],
        )
        for lines in invalid_streams:
            with self.subTest(lines=lines), self.assertRaises(FrontendAPIError) as ctx:
                list(_parse_sse(lines))
            self.assertEqual(ctx.exception.code, "INVALID_SSE")


class FastAPIClientTests(TestCase):
    @patch("network_agent_rag.frontend.client.urlopen")
    def test_get_history_encodes_session_and_returns_json(self, urlopen) -> None:
        urlopen.return_value = _Response(
            b'{"session_id":"demo:1","messages":[{"role":"user","content":"hi"}]}'
        )
        client = FastAPIClient("http://127.0.0.1:8000/", timeout=12.0)

        result = client.get_history("demo:1")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:8000/api/v1/history?session_id=demo%3A1",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 12.0)
        self.assertEqual(result["messages"][0]["content"], "hi")

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_stream_chat_posts_json_and_yields_complete_stream(self, urlopen) -> None:
        urlopen.return_value = _Response(
            b"event: start\n"
            b'data: {"session_id":"demo"}\n\n'
            b"event: answer\n"
            b'data: {"answer":"ok"}\n\n'
        )
        client = FastAPIClient("http://agent.local")

        events = list(client.stream_chat("demo", "Check SW1"))

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://agent.local/api/v1/chat")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data),
            {"session_id": "demo", "query": "Check SW1"},
        )
        self.assertEqual(request.get_header("Accept"), "text/event-stream")
        self.assertEqual([event.event for event in events], ["start", "answer"])

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_rejects_stream_without_answer_or_error(self, urlopen) -> None:
        urlopen.return_value = _Response(
            b"event: start\ndata: {\"session_id\":\"demo\"}\n\n"
        )

        with self.assertRaises(FrontendAPIError) as ctx:
            list(FastAPIClient("http://agent.local").stream_chat("demo", "query"))

        self.assertEqual(ctx.exception.code, "INCOMPLETE_STREAM")

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_normalizes_http_and_connection_errors(self, urlopen) -> None:
        http_error = HTTPError(
            "http://agent.local/api/v1/history",
            503,
            "Service Unavailable",
            {},
            BytesIO(b'{"detail":"Agent workflow is not configured"}'),
        )
        urlopen.side_effect = http_error
        client = FastAPIClient("http://agent.local")

        with self.assertRaises(FrontendAPIError) as ctx:
            client.get_history("demo")
        self.assertEqual(ctx.exception.code, "HTTP_503")
        self.assertEqual(ctx.exception.message, "Agent workflow is not configured")

        urlopen.side_effect = URLError("connection refused")
        with self.assertRaises(FrontendAPIError) as ctx:
            client.get_history("demo")
        self.assertEqual(ctx.exception.code, "CONNECTION_ERROR")


class StreamlitAppTests(TestCase):
    app_path = (
        Path(__file__).parents[1]
        / "src"
        / "network_agent_rag"
        / "frontend"
        / "app.py"
    )

    def setUp(self) -> None:
        warning_patcher = patch(
            "streamlit.runtime.scriptrunner_utils.script_run_context._LOGGER.warning"
        )
        warning_patcher.start()
        self.addCleanup(warning_patcher.stop)

    def test_maps_diagnosis_nodes_to_chinese_progress_labels(self) -> None:
        for node in (
            "TopologyTool",
            "MonitorTool",
            "LogTool",
            "RAG",
            "EvidenceCorrelator",
        ):
            self.assertIn(node, NODE_LABELS)
            self.assertTrue(NODE_LABELS[node])

    def test_maps_multi_agent_nodes_to_chinese_progress_labels(self) -> None:
        for node in (
            "Supervisor",
            "TopologyAgent",
            "LogAgent",
            "DiagnosisAgent",
            "RepairAgent",
            "ReportAgent",
        ):
            self.assertIn(node, NODE_LABELS)
            self.assertTrue(NODE_LABELS[node])

    def test_renders_single_page_console_without_calling_the_backend(self) -> None:
        app = AppTest.from_file(str(self.app_path)).run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.title[0].value, "企业网络智能运维 Agent")
        self.assertEqual(
            [heading.value for heading in app.subheader],
            ["AI 对话", "引用文档", "网络拓扑路径", "实时设备状态"],
        )
        self.assertEqual(len(app.text_input), 2)
        self.assertEqual(len(app.button), 1)
        self.assertEqual(len(app.chat_input), 1)

    def test_renders_latest_answer_sources_path_metrics_and_interfaces(self) -> None:
        app = AppTest.from_file(str(self.app_path)).run()
        app.session_state["active_session_id"] = "demo"
        app.session_state["backend_url"] = "http://127.0.0.1:8000"
        app.session_state["messages"] = [
            {"role": "user", "content": "Why is SW1 dropping packets?"},
            {"role": "assistant", "content": "Inspect the uplink."},
        ]
        app.session_state["last_response"] = {
            "source_documents": [
                {
                    "page_content": "Check CRC counters.",
                    "metadata": {
                        "source": "manual.md",
                        "title_path": "Interfaces / Errors",
                        "page": 3,
                    },
                }
            ],
            "topology_context": {"path": ["SW1", "SW2", "Core"]},
            "metrics": {
                "status": "degraded",
                "cpu_percent": 82.5,
                "memory_percent": 71.0,
                "traffic_in_mbps": 420.6,
                "traffic_out_mbps": 390.2,
                "packet_loss_percent": 2.4,
                "interfaces": [
                    {
                        "interface_name": "Gi0/1",
                        "admin_status": "up",
                        "oper_status": "down",
                    }
                ],
            },
            "error": None,
        }

        app.run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.chat_message), 2)
        self.assertTrue(any("manual.md" in item.label for item in app.expander))
        self.assertTrue(any("SW1 → SW2 → Core" in item.value for item in app.markdown))
        self.assertCountEqual(
            [item.label for item in app.metric],
            ["设备状态", "CPU", "内存", "入流量", "出流量", "丢包率"],
        )
        self.assertEqual(len(app.dataframe), 1)

    def test_renders_empty_states_and_unknown_context_as_json(self) -> None:
        app = AppTest.from_file(str(self.app_path)).run()
        app.session_state["active_session_id"] = "demo"
        app.session_state["backend_url"] = "http://127.0.0.1:8000"
        app.session_state["messages"] = []
        app.session_state["last_response"] = {
            "source_documents": [],
            "topology_context": {"nodes": ["SW1"]},
            "metrics": {"custom_health_score": 91},
            "error": None,
        }

        app.run()

        self.assertEqual(list(app.exception), [])
        self.assertTrue(any("暂无引用文档" in item.value for item in app.info))
        self.assertGreaterEqual(len(app.json), 2)

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_chat_submission_consumes_sse_and_updates_latest_response(
        self,
        urlopen,
    ) -> None:
        answer = {
            "session_id": "demo",
            "answer": "Inspect the uplink.",
            "intent": "hybrid",
            "rewritten_query": "Inspect SW1 uplink",
            "relevance_score": 0.9,
            "source_documents": [],
            "topology_context": {"path": ["SW1", "Core"]},
            "metrics": {"cpu_percent": 82.5},
            "iteration": 1,
            "error": None,
        }
        urlopen.return_value = _Response(
            b"event: start\ndata: {\"session_id\":\"demo\"}\n\n"
            b"event: node\ndata: {\"node\":\"Retrieval\",\"iteration\":1}\n\n"
            + f"event: answer\ndata: {json.dumps(answer)}\n\n".encode()
        )
        app = AppTest.from_file(str(self.app_path)).run()
        app.session_state["active_session_id"] = "demo"

        app.chat_input[0].set_value("Diagnose SW1").run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.chat_message), 2)
        self.assertEqual(app.session_state["last_response"]["answer"], answer["answer"])
        self.assertEqual(
            [message["role"] for message in app.session_state["messages"]],
            ["user", "assistant"],
        )

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_sidebar_loads_history_for_the_selected_session(self, urlopen) -> None:
        urlopen.return_value = _Response(
            b'{"session_id":"saved:1","messages":['
            b'{"role":"user","content":"Saved question"},'
            b'{"role":"assistant","content":"Saved answer"}]}'
        )
        app = AppTest.from_file(str(self.app_path)).run()
        app.text_input[0].set_value("http://agent.local")
        app.text_input[1].set_value("saved:1")

        app.button[0].click().run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.session_state["active_session_id"], "saved:1")
        self.assertEqual(
            [message["content"] for message in app.session_state["messages"]],
            ["Saved question", "Saved answer"],
        )
        self.assertIsNone(app.session_state["last_response"])

    @patch("network_agent_rag.frontend.client.urlopen")
    def test_failed_chat_clears_stale_details_without_fake_answer(self, urlopen) -> None:
        urlopen.side_effect = URLError("connection refused")
        app = AppTest.from_file(str(self.app_path)).run()
        app.session_state["messages"] = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        app.session_state["last_response"] = {
            "answer": "Previous answer",
            "source_documents": [{"page_content": "Old evidence", "metadata": {}}],
            "topology_context": {"path": ["Old-SW"]},
            "metrics": {"cpu_percent": 10},
            "error": None,
        }

        app.chat_input[0].set_value("New failing question").run()

        self.assertEqual(list(app.exception), [])
        self.assertIsNone(app.session_state["last_response"])
        self.assertEqual(
            [message["role"] for message in app.session_state["messages"]],
            ["user", "assistant", "user"],
        )
        self.assertTrue(any("CONNECTION_ERROR" in item.value for item in app.error))
