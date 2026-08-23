"""Tests for deterministic, read-only network log tools."""

from __future__ import annotations

from importlib import import_module
import unittest

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


class LogToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = import_module("network_agent_rag.packs.networkops.agents")
        self.tool = getattr(self.agents, "query_logs", None)

    def test_exports_structured_read_only_tool_for_toolnode(self) -> None:
        self.assertIsInstance(self.tool, BaseTool)
        self.assertEqual(
            set(self.tool.args),
            {"source_ids", "start", "end", "query", "limit"},
        )
        self.assertEqual(self.agents.LOG_TOOLS, [self.tool])

        builder = StateGraph(MessagesState)
        builder.add_node("logs", ToolNode(self.agents.LOG_TOOLS))
        builder.add_edge(START, "logs")
        builder.add_edge("logs", END)
        graph = builder.compile()
        result = graph.invoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "query_logs",
                                "args": {
                                    "source_ids": ["SW1", "SW2"],
                                    "start": "2026-07-12T08:30:00+08:00",
                                    "end": "2026-07-12T09:00:00+08:00",
                                    "query": "crc optical",
                                    "limit": 10,
                                },
                                "id": "log-call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        )
        self.assertEqual(result["messages"][-1].name, "query_logs")

    def test_filters_redacted_records_and_returns_evidence_references(self) -> None:
        result = self.tool.invoke(
            {
                "source_ids": [" sw1 ", "SW2"],
                "start": "2026-07-12T08:30:00+08:00",
                "end": "2026-07-12T09:00:00+08:00",
                "query": "crc optical",
                "limit": 10,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            [record["event_type"] for record in result["records"]],
            ["crc_threshold", "low_optical_rx"],
        )
        self.assertEqual(result["evidence_refs"], ["LOG-2001", "LOG-2002"])
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        serialized = str(result).casefold()
        self.assertNotIn("password", serialized)
        self.assertNotIn("community", serialized)

    def test_validates_targets_time_query_and_limit_without_raising(self) -> None:
        valid = {
            "source_ids": ["SW1"],
            "start": "2026-07-12T08:30:00+08:00",
            "end": "2026-07-12T09:00:00+08:00",
            "query": "crc",
            "limit": 10,
        }
        cases = (
            ({**valid, "source_ids": []}, "INVALID_ARGUMENT"),
            ({**valid, "source_ids": ["unknown"]}, "SOURCE_NOT_FOUND"),
            ({**valid, "start": "not-a-time"}, "INVALID_ARGUMENT"),
            ({**valid, "end": "2026-07-12T08:00:00+08:00"}, "INVALID_ARGUMENT"),
            ({**valid, "query": "  "}, "INVALID_ARGUMENT"),
            ({**valid, "limit": 0}, "INVALID_ARGUMENT"),
        )
        for arguments, code in cases:
            with self.subTest(arguments=arguments):
                result = self.tool.invoke(arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["records"], [])

    def test_repeated_queries_return_the_same_snapshot(self) -> None:
        arguments = {
            "source_ids": ["SW1", "SW2"],
            "start": "2026-07-12T08:30:00+08:00",
            "end": "2026-07-12T09:00:00+08:00",
            "query": "crc optical",
            "limit": 10,
        }
        self.assertEqual(self.tool.invoke(arguments), self.tool.invoke(arguments))


if __name__ == "__main__":
    unittest.main()
