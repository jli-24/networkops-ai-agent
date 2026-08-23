"""Tests for the v0.2 supervisor-led multi-agent workflow."""

from __future__ import annotations

from collections.abc import Callable
import unittest

from langchain_core.documents import Document

from network_agent_rag.packs.networkops.agents import DiagnosisPlan
from network_agent_rag.packs.networkops.agents.multi_agent import (
    MultiAgentState,
    create_multi_agent_workflow,
)


def incident_plan(
    *,
    required_agents: list[str] | None = None,
    required_sources: list[str] | None = None,
    requires_repair: bool = True,
) -> dict[str, object]:
    return {
        "analysis": {
            "devices": ["SW1", "SW2"],
            "interfaces": ["SW1:Gi0/1", "SW2:Gi0/24"],
            "symptom": "链路丢包",
            "start_time": "2026-07-12T08:30:00+08:00",
            "end_time": "2026-07-12T09:00:00+08:00",
            "required_sources": required_sources
            or ["topology", "monitoring", "logs", "knowledge"],
        },
        "required_agents": required_agents
        or ["topology", "logs", "diagnosis", "repair", "report"],
        "requires_repair": requires_repair,
    }


def topology(plan: DiagnosisPlan) -> dict[str, object]:
    return {
        "path": ["SW1", "SW2"],
        "links": [
            {
                "source": "SW1",
                "target": "SW2",
                "type": "connect",
                "source_interface": "Gi0/1",
                "target_interface": "Gi0/24",
                "evidence_ref": "TOPO-1001",
            }
        ],
    }


def logs(plan: DiagnosisPlan) -> dict[str, object]:
    return {
        "ok": True,
        "records": [
            {
                "source_id": "SW1",
                "interface_name": "Gi0/1",
                "timestamp": "2026-07-12T08:50:00+08:00",
                "message": "CRC errors and optical receive power alarm",
                "evidence_ref": "LOG-2001",
            }
        ],
    }


def metrics(
    plan: DiagnosisPlan,
    topology_context: dict[str, object],
) -> dict[str, object]:
    return {
        "status": "degraded",
        "interfaces": [
            {
                "device_id": "SW1",
                "interface_name": "Gi0/1",
                "oper_status": "up",
                "crc_errors_delta": 25,
                "input_errors_delta": 12,
                "optical_rx_dbm": -18.5,
                "optical_rx_low_threshold_dbm": -17.0,
                "evidence_ref": "METRIC-3001",
            }
        ],
    }


def documents(query: str) -> list[Document]:
    return [
        Document(
            page_content="光模块退化会导致 CRC、optical 光功率告警和链路丢包。",
            metadata={"relevance_score": 0.95, "evidence_ref": "KB-4001"},
        )
    ]


def grade(state: MultiAgentState) -> dict[str, object]:
    return {"relevance_score": 0.95, "feedback": "相关历史案例"}


class MultiAgentWorkflowTests(unittest.TestCase):
    def _graph(self, **overrides: object):
        arguments: dict[str, object] = {
            "plan_incident": lambda query, incident_id: incident_plan(),
            "retrieve_topology": topology,
            "retrieve_logs": logs,
            "retrieve_metrics": metrics,
            "retrieve_documents": documents,
            "grade_documents": grade,
            "rewrite_query": lambda state: f"{state['rewritten_query']} 光模块 CRC",
        }
        arguments.update(overrides)
        return create_multi_agent_workflow(**arguments)

    def test_exports_state_and_builds_supervisor_with_five_agents(self) -> None:
        import network_agent_rag
        import network_agent_rag.packs.networkops.agents as agents

        self.assertEqual(network_agent_rag.__version__, "0.15.0")
        self.assertIs(agents.create_multi_agent_workflow, create_multi_agent_workflow)
        graph = self._graph()
        nodes = set(graph.get_graph().nodes)
        self.assertTrue(
            {
                "Supervisor",
                "TopologyAgent",
                "LogAgent",
                "DiagnosisAgent",
                "RepairAgent",
                "ReportAgent",
            }.issubset(nodes)
        )

    def test_full_workflow_communicates_through_shared_state(self) -> None:
        result = self._graph().invoke(
            {"user_query": "分析 SW1 到 SW2 丢包", "incident_id": "INC-1001"}
        )

        self.assertEqual(result["incident_id"], "INC-1001")
        self.assertEqual(
            result["completed_agents"],
            ["topology", "logs", "diagnosis", "repair", "report"],
        )
        self.assertEqual(result["topology_context"]["path"], ["SW1", "SW2"])
        self.assertEqual(result["log_evidence"][0]["evidence_ref"], "LOG-2001")
        self.assertEqual(result["metrics"], result["device_evidence"])
        self.assertEqual(
            result["diagnosis_result"]["hypotheses"][0]["confidence_percent"],
            80,
        )
        self.assertEqual(result["repair_plan"]["execution_status"], "not_executed")
        self.assertTrue(result["repair_plan"]["requires_human_approval"])
        self.assertEqual(result["answer"], result["report"])
        self.assertIn("未执行", result["report"])
        self.assertIsNone(result["error"])

    def test_supervisor_skips_unrequested_agents(self) -> None:
        calls: list[str] = []
        graph = self._graph(
            plan_incident=lambda query, incident_id: incident_plan(
                required_agents=["diagnosis", "report"],
                required_sources=["monitoring", "knowledge"],
                requires_repair=False,
            ),
            retrieve_topology=lambda plan: calls.append("topology") or {},
            retrieve_logs=lambda plan: calls.append("logs") or {"ok": True, "records": []},
            retrieve_metrics=lambda plan, context: calls.append("monitoring")
            or metrics(plan, context),
            retrieve_documents=lambda query: calls.append("knowledge") or documents(query),
        )

        result = graph.invoke({"user_query": "分析接口告警"})

        self.assertEqual(calls, ["monitoring", "knowledge"])
        self.assertEqual(result["completed_agents"], ["diagnosis", "report"])
        self.assertIsNone(result["repair_plan"])

    def test_low_quality_documents_are_rewritten_before_diagnosis(self) -> None:
        queries: list[str] = []
        scores = iter((0.2, 0.9))

        def retrieve(query: str) -> list[Document]:
            queries.append(query)
            return documents(query)

        result = self._graph(
            retrieve_documents=retrieve,
            grade_documents=lambda state: {
                "relevance_score": next(scores),
                "feedback": "需要更具体的故障特征",
            },
        ).invoke({"user_query": "链路异常"})

        self.assertEqual(queries, ["链路异常", "链路异常 光模块 CRC"])
        self.assertEqual(result["diagnosis_iteration"], 2)
        self.assertEqual(result["grading_feedback"], "需要更具体的故障特征")
        self.assertIn("diagnosis", result["completed_agents"])

    def test_three_low_quality_searches_exclude_rag_from_diagnosis_score(self) -> None:
        result = self._graph(
            grade_documents=lambda state: {
                "relevance_score": 0.2,
                "feedback": "历史案例不够相关",
            },
        ).invoke({"user_query": "链路异常"})

        hypothesis = result["diagnosis_result"]["hypotheses"][0]
        self.assertEqual(result["diagnosis_iteration"], 3)
        self.assertEqual(hypothesis["confidence_percent"], 70)
        self.assertTrue(result["diagnosis_result"]["remaining_uncertainties"])

    def test_report_rejection_retries_at_most_three_times(self) -> None:
        generated = 0

        def report(state: MultiAgentState) -> str:
            nonlocal generated
            generated += 1
            return f"report-{generated}"

        result = self._graph(
            generate_report=report,
            check_report=lambda state: {
                "approved": False,
                "feedback": "缺少证据引用",
            },
        ).invoke({"user_query": "诊断"})

        self.assertEqual(generated, 3)
        self.assertEqual(result["report_iteration"], 3)
        self.assertEqual(result["answer"], "report-3")
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])

    def test_handoff_limit_counts_quality_redispatches(self) -> None:
        result = self._graph(
            plan_incident=lambda query, incident_id: incident_plan(
                required_agents=["diagnosis", "report"],
                required_sources=["monitoring", "knowledge"],
                requires_repair=False,
            ),
            grade_documents=lambda state: {
                "relevance_score": 0.2,
                "feedback": "需要重写",
            },
            max_handoffs=1,
        ).invoke({"user_query": "链路异常"})

        self.assertEqual(result["handoff_count"], 1)
        self.assertIn("MAX_HANDOFFS_REACHED", result["error"])
        self.assertIsNone(result["diagnosis_result"])

    def test_transient_agent_failure_retries_and_optional_failure_degrades(self) -> None:
        attempts = 0

        def flaky(plan: DiagnosisPlan) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary")
            return topology(plan)

        recovered = self._graph(retrieve_topology=flaky).invoke(
            {"user_query": "诊断"}
        )
        self.assertEqual(attempts, 3)
        self.assertNotIn("topology", recovered["agent_errors"])

        degraded = self._graph(
            retrieve_logs=lambda plan: (_ for _ in ()).throw(ValueError("bad query"))
        ).invoke({"user_query": "诊断"})
        self.assertIn("logs", degraded["agent_errors"])
        self.assertIn("report", degraded["completed_agents"])
        self.assertTrue(degraded["answer"])

    def test_invalid_supervisor_plan_terminates_structurally(self) -> None:
        result = self._graph(
            plan_incident=lambda query, incident_id: incident_plan(
                required_agents=["report", "diagnosis"],
            )
        ).invoke({"user_query": "诊断"})

        self.assertIn("Supervisor", result["error"])
        self.assertTrue(result["answer"])


if __name__ == "__main__":
    unittest.main()
