"""Tests for deterministic network monitoring LangGraph tools."""

from __future__ import annotations

from importlib import import_module
import unittest

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


SNAPSHOT_TIME = "2026-07-12T09:00:00+08:00"


class MonitoringToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = import_module("network_agent_rag.agents")

    def _tool(self, name: str) -> BaseTool:
        candidate = getattr(self.agents, name, None)
        self.assertIsInstance(candidate, BaseTool)
        return candidate

    def test_exports_structured_tools_for_langgraph(self) -> None:
        tools = getattr(self.agents, "MONITORING_TOOLS", None)
        self.assertIsInstance(tools, list)
        self.assertEqual(
            [tool.name for tool in tools],
            ["query_device_status", "query_interface", "query_alarm"],
        )
        self.assertTrue(all(tool.description for tool in tools))
        self.assertEqual(
            set(self._tool("query_device_status").args),
            {"device_id"},
        )
        self.assertEqual(
            set(self._tool("query_interface").args),
            {"device_id", "interface_name"},
        )
        self.assertEqual(
            set(self._tool("query_alarm").args),
            {"device_id", "severity", "active_only"},
        )
        node = ToolNode(tools)
        self.assertIsInstance(node, ToolNode)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        result = graph.invoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "query_device_status",
                                "args": {"device_id": "core-sw-01"},
                                "id": "monitoring-call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        )
        self.assertEqual(result["messages"][-1].name, "query_device_status")
        self.assertIn("core-sw-01", str(result["messages"][-1].content))

    def test_queries_online_degraded_and_offline_device_status(self) -> None:
        tool = self._tool("query_device_status")

        online = tool.invoke({"device_id": "  CORE-SW-01  "})
        degraded = tool.invoke({"device_id": "edge-rtr-01"})
        offline = tool.invoke({"device_id": "access-sw-01"})

        self.assertEqual(online["device_id"], "core-sw-01")
        self.assertEqual(online["status"], "online")
        self.assertEqual(online["cpu_percent"], 32.5)
        self.assertEqual(online["memory_percent"], 58.2)
        self.assertEqual(online["traffic_in_mbps"], 810.4)
        self.assertEqual(online["traffic_out_mbps"], 625.8)
        self.assertEqual(online["packet_loss_percent"], 0.05)
        self.assertEqual(online["interfaces"], {"total": 3, "up": 3, "down": 0})
        self.assertEqual(online["observed_at"], SNAPSHOT_TIME)
        self.assertEqual(degraded["status"], "degraded")
        self.assertEqual(degraded["interfaces"], {"total": 2, "up": 1, "down": 1})
        self.assertEqual(offline["status"], "offline")
        self.assertEqual(offline["interfaces"], {"total": 2, "up": 0, "down": 2})

    def test_queries_all_interfaces_or_one_canonical_interface(self) -> None:
        tool = self._tool("query_interface")

        all_interfaces = tool.invoke({"device_id": "edge-rtr-01"})
        one_interface = tool.invoke(
            {
                "device_id": " EDGE-RTR-01 ",
                "interface_name": " gigabitethernet0/0 ",
            }
        )

        self.assertTrue(all_interfaces["ok"])
        self.assertEqual(all_interfaces["count"], 2)
        self.assertEqual(len(all_interfaces["interfaces"]), 2)
        self.assertEqual(one_interface["count"], 1)
        self.assertEqual(one_interface["observed_at"], SNAPSHOT_TIME)
        interface = one_interface["interfaces"][0]
        self.assertEqual(interface["interface_name"], "GigabitEthernet0/0")
        self.assertEqual(interface["admin_status"], "up")
        self.assertEqual(interface["oper_status"], "up")
        self.assertIn("traffic_in_mbps", interface)
        self.assertIn("traffic_out_mbps", interface)
        self.assertIn("packet_loss_percent", interface)

    def test_filters_alarms_and_allows_empty_success_results(self) -> None:
        tool = self._tool("query_alarm")

        active = tool.invoke({})
        critical = tool.invoke({"severity": " CRITICAL "})
        all_core = tool.invoke({"device_id": "CORE-SW-01", "active_only": False})
        none = tool.invoke({"device_id": "access-sw-01", "severity": "minor"})

        self.assertTrue(active["ok"])
        self.assertEqual(active["count"], 2)
        self.assertTrue(all(alarm["status"] == "active" for alarm in active["alarms"]))
        self.assertEqual(critical["count"], 1)
        self.assertEqual(critical["alarms"][0]["severity"], "critical")
        self.assertEqual(all_core["count"], 2)
        self.assertEqual({alarm["status"] for alarm in all_core["alarms"]}, {"active", "cleared"})
        self.assertEqual(
            none,
            {
                "ok": True,
                "count": 0,
                "alarms": [],
                "observed_at": SNAPSHOT_TIME,
            },
        )
        self.assertEqual(active["observed_at"], SNAPSHOT_TIME)

    def test_returns_structured_errors_for_invalid_queries(self) -> None:
        device_tool = self._tool("query_device_status")
        interface_tool = self._tool("query_interface")
        alarm_tool = self._tool("query_alarm")

        cases = (
            (device_tool, {"device_id": "  "}, "INVALID_ARGUMENT"),
            (device_tool, {"device_id": "missing"}, "DEVICE_NOT_FOUND"),
            (
                interface_tool,
                {"device_id": "core-sw-01", "interface_name": "missing"},
                "INTERFACE_NOT_FOUND",
            ),
            (alarm_tool, {"severity": "info"}, "INVALID_ARGUMENT"),
            (alarm_tool, {"device_id": "missing"}, "DEVICE_NOT_FOUND"),
        )

        for tool, arguments, error_code in cases:
            with self.subTest(tool=tool.name, arguments=arguments):
                result = tool.invoke(arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], error_code)
                self.assertTrue(result["message"])

    def test_repeated_queries_return_the_same_snapshot(self) -> None:
        tool = self._tool("query_device_status")
        arguments = {"device_id": "core-sw-01"}
        self.assertEqual(tool.invoke(arguments), tool.invoke(arguments))

    def test_sw1_sw2_expose_physical_link_error_and_optical_metrics(self) -> None:
        device_tool = self._tool("query_device_status")
        interface_tool = self._tool("query_interface")

        sw1 = device_tool.invoke({"device_id": "sw1"})
        sw2_interface = interface_tool.invoke(
            {"device_id": "SW2", "interface_name": "gi0/24"}
        )["interfaces"][0]
        sw1_interface = interface_tool.invoke(
            {"device_id": "SW1", "interface_name": "Gi0/1"}
        )["interfaces"][0]

        self.assertEqual(sw1["device_id"], "SW1")
        self.assertEqual(sw1["status"], "degraded")
        for interface in (sw1_interface, sw2_interface):
            self.assertEqual(interface["admin_status"], "up")
            self.assertEqual(interface["oper_status"], "up")
            self.assertGreater(interface["crc_errors_delta"], 0)
            self.assertGreater(interface["input_errors_delta"], 0)
            self.assertLessEqual(
                interface["optical_rx_dbm"],
                interface["optical_rx_low_threshold_dbm"],
            )


if __name__ == "__main__":
    unittest.main()
