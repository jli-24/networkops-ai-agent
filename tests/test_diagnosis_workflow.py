"""Tests for the dedicated SW1-SW2 diagnosis workflow."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import unittest

from langchain_core.documents import Document
from langgraph.graph.state import CompiledStateGraph


def full_plan(query: str) -> dict[str, object]:
    return {
        "devices": ["SW1", "SW2"],
        "interfaces": ["SW1:Gi0/1", "SW2:Gi0/24"],
        "symptom": "链路丢包",
        "start_time": "2026-07-12T08:30:00+08:00",
        "end_time": "2026-07-12T09:00:00+08:00",
        "required_sources": ["topology", "monitoring", "logs", "knowledge"],
    }


def topology(plan: dict[str, object]) -> dict[str, object]:
    return {
        "path": ["SW1", "SW2"],
        "links": [
            {
                "source": "SW1",
                "target": "SW2",
                "source_interface": "Gi0/1",
                "target_interface": "Gi0/24",
                "type": "connect",
                "evidence_ref": "TOPO-1001",
            }
        ],
    }


def metrics(plan: dict[str, object], context: dict[str, object]) -> dict[str, object]:
    return {
        "interfaces": [
            {
                "device_id": "SW1",
                "interface_name": "Gi0/1",
                "admin_status": "up",
                "oper_status": "up",
                "crc_errors_delta": 320,
                "input_errors_delta": 340,
                "optical_rx_dbm": -19.8,
                "optical_rx_low_threshold_dbm": -18.0,
                "packet_loss_percent": 3.8,
                "evidence_ref": "METRIC-3001",
            },
            {
                "device_id": "SW2",
                "interface_name": "Gi0/24",
                "admin_status": "up",
                "oper_status": "up",
                "crc_errors_delta": 301,
                "input_errors_delta": 326,
                "optical_rx_dbm": -20.1,
                "optical_rx_low_threshold_dbm": -18.0,
                "packet_loss_percent": 3.6,
                "evidence_ref": "METRIC-3002",
            },
        ],
        "observed_at": "2026-07-12T09:00:00+08:00",
    }


def logs(plan: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "records": [
            {
                "evidence_ref": "LOG-2001",
                "timestamp": "2026-07-12T08:54:00+08:00",
                "source_id": "SW1",
                "interface_name": "Gi0/1",
                "event_type": "crc_threshold",
                "message": "CRC errors increased on SW1 Gi0/1",
            }
        ],
        "evidence_refs": ["LOG-2001"],
    }


def documents(query: str) -> list[Document]:
    return [
        Document(
            page_content=(
                "历史案例：链路保持 up，CRC 持续增长且接收光功率低于阈值，"
                "根因为光模块退化。"
            ),
            metadata={
                "source": "optical_case.md",
                "evidence_ref": "KB-4001",
                "relevance_score": 0.95,
            },
        )
    ]


class DiagnosisWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = import_module("network_agent_rag.packs.networkops.agents")
        self.factory: Callable[..., CompiledStateGraph] = getattr(
            self.agents, "create_network_diagnosis_workflow"
        )

    def _graph(self, **overrides: object) -> CompiledStateGraph:
        arguments: dict[str, object] = {
            "analyze_query": full_plan,
            "retrieve_topology": topology,
            "retrieve_metrics": metrics,
            "retrieve_logs": logs,
            "retrieve_documents": documents,
        }
        arguments.update(overrides)
        return self.factory(**arguments)

    def test_exports_types_and_builds_eight_named_nodes(self) -> None:
        for name in (
            "EvidenceSource",
            "DiagnosisPlan",
            "RootCauseHypothesis",
            "DiagnosisState",
        ):
            self.assertIsNotNone(getattr(self.agents, name, None))

        graph = self._graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        nodes = {name for name in graph.get_graph().nodes if not name.startswith("__")}
        self.assertEqual(
            nodes,
            {
                "QueryAnalyzer",
                "TopologyTool",
                "MonitorTool",
                "LogTool",
                "RAG",
                "EvidenceCorrelator",
                "Generator",
                "HallucinationChecker",
            },
        )

    def test_full_evidence_scores_optical_module_fault_at_eighty(self) -> None:
        result = self._graph().invoke(
            {"user_query": "SW1 到 SW2 链路丢包，请结合拓扑、日志和知识库分析"}
        )

        hypothesis = result["hypotheses"][0]
        self.assertEqual(hypothesis["cause"], "光模块异常")
        self.assertEqual(hypothesis["confidence_percent"], 80)
        self.assertIn("规则诊断置信度 80%", result["answer"])
        self.assertIn("CRC", result["answer"])
        self.assertIn("光功率", result["answer"])
        self.assertIn("维护窗口", result["answer"])
        self.assertIn("人工批准", result["answer"])
        self.assertIn("验证", result["answer"])
        self.assertIn("2026-07-12T08:54:00+08:00", result["answer"])
        self.assertIsNone(result["error"])

    def test_missing_each_evidence_category_lowers_score(self) -> None:
        cases = {
            "topology": {"retrieve_topology": lambda plan: {}},
            "crc": {
                "retrieve_metrics": lambda plan, context: {
                    "interfaces": [
                        {
                            **interface,
                            "crc_errors_delta": 0,
                            "input_errors_delta": 0,
                        }
                        for interface in metrics(plan, context)["interfaces"]
                    ]
                }
            },
            "optics": {
                "retrieve_metrics": lambda plan, context: {
                    "interfaces": [
                        {
                            **interface,
                            "optical_rx_dbm": -16.0,
                        }
                        for interface in metrics(plan, context)["interfaces"]
                    ]
                }
            },
            "logs": {"retrieve_logs": lambda plan: {"ok": True, "records": []}},
            "knowledge": {"retrieve_documents": lambda query: []},
        }
        for name, override in cases.items():
            with self.subTest(name=name):
                result = self._graph(**override).invoke({"user_query": "diagnose"})
                self.assertLess(result["hypotheses"][0]["confidence_percent"], 80)

    def test_unrelated_physical_link_does_not_receive_topology_points(self) -> None:
        unrelated = {
            "path": ["OTHER-1", "OTHER-2"],
            "links": [
                {
                    "source": "OTHER-1",
                    "target": "OTHER-2",
                    "source_interface": "Gi9/1",
                    "target_interface": "Gi9/2",
                    "type": "connect",
                    "evidence_ref": "TOPO-WRONG",
                }
            ],
        }
        result = self._graph(
            retrieve_topology=lambda plan: unrelated,
        ).invoke({"user_query": "diagnose"})

        self.assertEqual(result["hypotheses"][0]["confidence_percent"], 70)
        self.assertNotIn("TOPO-WRONG", result["evidence_refs"])

    def test_stale_logs_and_low_relevance_documents_do_not_add_points(self) -> None:
        stale = logs(full_plan("diagnose"))
        stale["records"][0]["timestamp"] = "2026-07-11T08:54:00+08:00"
        low_relevance = documents("diagnose")
        low_relevance[0].metadata["relevance_score"] = 0.2

        result = self._graph(
            retrieve_logs=lambda plan: stale,
            retrieve_documents=lambda query: low_relevance,
        ).invoke({"user_query": "diagnose"})

        self.assertEqual(result["hypotheses"][0]["confidence_percent"], 60)
        self.assertNotIn("LOG-2001", result["evidence_refs"])
        self.assertNotIn("KB-4001", result["evidence_refs"])

    def test_no_evidence_marks_root_cause_as_unverified(self) -> None:
        result = self._graph(
            retrieve_topology=lambda plan: {},
            retrieve_metrics=lambda plan, context: {},
            retrieve_logs=lambda plan: {"ok": True, "records": []},
            retrieve_documents=lambda query: [],
        ).invoke({"user_query": "diagnose"})

        hypothesis = result["hypotheses"][0]
        self.assertEqual(hypothesis["confidence_percent"], 0)
        self.assertIn("待验证", hypothesis["cause"])
        self.assertNotIn("根因：光模块异常\n", result["answer"])

    def test_conditional_edges_skip_unrequested_sources_in_fixed_order(self) -> None:
        calls: list[str] = []

        def plan(query: str) -> dict[str, object]:
            value = full_plan(query)
            value["required_sources"] = ["topology", "logs"]
            return value

        result = self._graph(
            analyze_query=plan,
            retrieve_topology=lambda value: calls.append("topology") or topology(value),
            retrieve_metrics=lambda value, context: calls.append("monitoring") or {},
            retrieve_logs=lambda value: calls.append("logs") or logs(value),
            retrieve_documents=lambda query: calls.append("knowledge") or [],
        ).invoke({"user_query": "diagnose"})

        self.assertEqual(calls, ["topology", "logs"])
        self.assertEqual(result["metrics"], {})
        self.assertEqual(result["documents"], [])

    def test_transient_reader_error_retries_three_times(self) -> None:
        attempts = 0

        def flaky(plan: dict[str, object]) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary")
            return topology(plan)

        result = self._graph(retrieve_topology=flaky).invoke(
            {"user_query": "diagnose"}
        )
        self.assertEqual(attempts, 3)
        self.assertIsNone(result["error"])

    def test_exhausted_reader_retries_end_with_structured_error(self) -> None:
        attempts = 0

        def unavailable(plan: dict[str, object]) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("log backend unavailable")

        result = self._graph(retrieve_logs=unavailable).invoke(
            {"user_query": "diagnose"}
        )

        self.assertEqual(attempts, 3)
        self.assertIn("LogTool", result["error"])
        self.assertIn("ConnectionError", result["error"])
        self.assertIn("未执行任何网络变更", result["answer"])

    def test_checker_can_regenerate_but_stops_after_three_answers(self) -> None:
        generated = 0

        def generate(state: dict[str, object]) -> str:
            nonlocal generated
            generated += 1
            return f"answer-{generated} 光模块异常 规则诊断置信度 80% 人工批准"

        result = self._graph(
            generate_answer=generate,
            check_answer=lambda state: {
                "approved": False,
                "feedback": "missing evidence detail",
            },
        ).invoke({"user_query": "diagnose"})

        self.assertEqual(generated, 3)
        self.assertEqual(result["iteration"], 3)
        self.assertEqual(result["answer"].split()[0], "answer-3")
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])

    def test_default_checker_rejects_unsafe_or_ungrounded_custom_answer(self) -> None:
        generated = 0

        def unsafe(state: dict[str, object]) -> str:
            nonlocal generated
            generated += 1
            return (
                "光模块异常 规则诊断置信度 80% CRC 光功率 人工批准 验证；"
                "已自动执行修复并重启接口。"
            )

        result = self._graph(generate_answer=unsafe).invoke(
            {"user_query": "diagnose"}
        )

        self.assertEqual(generated, 3)
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])
        self.assertIn("unsafe", result["checker_feedback"])

    def test_default_checker_rejects_negated_cause_and_unknown_evidence_ref(self) -> None:
        result = self._graph(
            generate_answer=lambda state: (
                "根因：不是光模块异常\n"
                "规则诊断置信度 80%\n"
                + "\n".join(state["hypotheses"][0]["supporting_evidence"])
                + "\nTOPO-FAKE METRIC-3001 METRIC-3002 LOG-2001 KB-4001\n"
                "CRC 光功率 维护窗口 人工批准 验证 不会执行"
            )
        ).invoke({"user_query": "diagnose"})

        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])
        self.assertIn("unsupported", result["checker_feedback"])

    def test_contract_errors_terminate_with_structured_fallback(self) -> None:
        result = self._graph(analyze_query=lambda query: {"devices": []}).invoke(
            {"user_query": "diagnose"}
        )
        self.assertIn("ValueError", result["error"])
        self.assertTrue(result["answer"])


if __name__ == "__main__":
    unittest.main()
