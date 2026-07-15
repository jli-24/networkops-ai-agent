"""Top-level StateGraph coordinating NetworkOps specialist agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from typing_extensions import NotRequired, TypedDict
from uuid import uuid4

from langchain_core.documents import Document
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, RetryPolicy

from network_agent_rag.agents.diagnosis_workflow import DiagnosisPlan
from network_agent_rag.agents.multi_agent.diagnosis_agent import run_diagnosis_agent
from network_agent_rag.agents.multi_agent.log_agent import run_log_agent
from network_agent_rag.agents.multi_agent.repair_agent import run_repair_agent
from network_agent_rag.agents.multi_agent.report_agent import run_report_agent
from network_agent_rag.agents.multi_agent.state import (
    AgentName,
    MultiAgentState,
    RepairPlan,
    SupervisorPlan,
)
from network_agent_rag.agents.multi_agent.supervisor import (
    next_agent,
    validate_supervisor_plan,
)
from network_agent_rag.agents.multi_agent.topology_agent import run_topology_agent
from network_agent_rag.agents.workflow import (
    CheckerResult,
    DocumentGradeResult,
)


_NODE_TO_AGENT: dict[str, AgentName] = {
    "TopologyAgent": "topology",
    "LogAgent": "logs",
    "DiagnosisAgent": "diagnosis",
    "RepairAgent": "repair",
    "ReportAgent": "report",
}
_AGENT_TO_NODE = {agent: node for node, agent in _NODE_TO_AGENT.items()}
_FALLBACK_ANSWER = "多 Agent 诊断未能完成；未执行任何网络变更，请人工检查后重试。"


class _MultiAgentInput(TypedDict):
    user_query: str
    incident_id: NotRequired[str]


def create_multi_agent_workflow(
    *,
    plan_incident: Callable[[str, str], SupervisorPlan],
    retrieve_topology: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_logs: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_metrics: Callable[
        [DiagnosisPlan, dict[str, object]], dict[str, object]
    ]
    | None = None,
    retrieve_documents: Callable[[str], list[Document]] | None = None,
    grade_documents: Callable[[MultiAgentState], DocumentGradeResult] | None = None,
    rewrite_query: Callable[[MultiAgentState], str] | None = None,
    build_repair_plan: Callable[[MultiAgentState], RepairPlan] | None = None,
    generate_report: Callable[[MultiAgentState], str] | None = None,
    check_report: Callable[[MultiAgentState], CheckerResult] | None = None,
    max_quality_iterations: int = 3,
    max_retry_attempts: int = 3,
    max_handoffs: int = 12,
) -> CompiledStateGraph:
    """Compile the synchronous supervisor-led multi-agent workflow."""

    for name, value, upper in (
        ("max_quality_iterations", max_quality_iterations, 3),
        ("max_retry_attempts", max_retry_attempts, 3),
        ("max_handoffs", max_handoffs, 32),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be between 1 and {upper}")

    retry_policy = RetryPolicy(
        initial_interval=0.0,
        backoff_factor=1.0,
        max_interval=0.0,
        max_attempts=max_retry_attempts,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError),
    )

    def supervisor(raw_state: _MultiAgentInput | MultiAgentState) -> dict[str, object]:
        if "task_plan" not in raw_state:
            query = raw_state.get("user_query", "")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("user_query must be a non-empty string")
            incident_id = raw_state.get("incident_id") or uuid4().hex
            if not isinstance(incident_id, str) or not incident_id.strip():
                raise ValueError("incident_id must be a non-empty string")
            plan = plan_incident(query.strip(), incident_id.strip())
            validate_supervisor_plan(plan)
            typed_plan = cast(SupervisorPlan, plan)
            pending = list(typed_plan["required_agents"])
            return {
                "user_query": query.strip(),
                "rewritten_query": query.strip(),
                "intent": "hybrid",
                "documents": [],
                "relevance_score": None,
                "grading_feedback": None,
                "topology_context": {},
                "metrics": {},
                "answer": "",
                "iteration": 0,
                "checker_feedback": None,
                "error": None,
                "incident_id": incident_id.strip(),
                "task_plan": typed_plan,
                "pending_agents": pending,
                "completed_agents": [],
                "current_agent": next_agent(typed_plan, pending),
                "device_evidence": {},
                "log_evidence": [],
                "diagnosis_result": None,
                "repair_plan": None,
                "report": "",
                "agent_errors": {},
                "handoff_count": 0,
                "diagnosis_iteration": 0,
                "report_iteration": 0,
                "report_feedback": None,
            }

        state = cast(MultiAgentState, raw_state)
        if state["handoff_count"] >= max_handoffs and state["pending_agents"]:
            return {
                "pending_agents": [],
                "current_agent": None,
                "answer": state["answer"] or _FALLBACK_ANSWER,
                "error": f"MAX_HANDOFFS_REACHED: exceeded {max_handoffs} agent handoffs",
            }
        pending = list(state["pending_agents"])
        errors = dict(state["agent_errors"])
        if (
            "repair" in pending
            and "diagnosis" in state["completed_agents"]
            and state["diagnosis_result"] is None
        ):
            pending.remove("repair")
            errors["repair"] = "skipped because diagnosis_result is unavailable"
        return {
            "pending_agents": pending,
            "agent_errors": errors,
            "current_agent": next_agent(state["task_plan"], pending),
        }

    def topology_agent(state: MultiAgentState) -> dict[str, object]:
        return run_topology_agent(state, retrieve_topology)

    def log_agent(state: MultiAgentState) -> dict[str, object]:
        return run_log_agent(state, retrieve_logs)

    def diagnosis_agent(state: MultiAgentState) -> dict[str, object]:
        return run_diagnosis_agent(
            state,
            retrieve_metrics=retrieve_metrics,
            retrieve_documents=retrieve_documents,
            grade_documents=grade_documents,
            rewrite_query=rewrite_query,
            max_quality_iterations=max_quality_iterations,
        )

    def repair_agent(state: MultiAgentState) -> dict[str, object]:
        return run_repair_agent(state, build_repair_plan)

    def report_agent(state: MultiAgentState) -> dict[str, object]:
        return run_report_agent(
            state,
            generate_report=generate_report,
            check_report=check_report,
            max_quality_iterations=max_quality_iterations,
        )

    def handle_error(state: MultiAgentState, error: NodeError) -> Command:
        diagnostic = f"{error.node}: {type(error.error).__name__}: {error.error}"
        if error.node == "Supervisor":
            return Command(update=_terminal_state(state, diagnostic), goto=END)
        agent = _NODE_TO_AGENT.get(error.node)
        if agent is None:
            return Command(update=_terminal_state(state, diagnostic), goto=END)
        errors = dict(state.get("agent_errors", {}))
        errors[agent] = diagnostic
        pending = [name for name in state.get("pending_agents", []) if name != agent]
        completed = list(state.get("completed_agents", []))
        if agent != "report" and agent not in completed:
            completed.append(agent)
        if agent == "diagnosis":
            pending = [name for name in pending if name != "repair"]
            errors["repair"] = "skipped because DiagnosisAgent failed"
        update: dict[str, object] = {
            "agent_errors": errors,
            "pending_agents": pending,
            "completed_agents": completed,
            "current_agent": None,
            "handoff_count": state.get("handoff_count", 0) + 1,
        }
        if agent == "report":
            update.update(
                {
                    "answer": state.get("answer") or _FALLBACK_ANSWER,
                    "report": state.get("report") or _FALLBACK_ANSWER,
                    "error": diagnostic,
                }
            )
            return Command(update=update, goto=END)
        return Command(update=update, goto="Supervisor")

    def route_supervisor(state: MultiAgentState) -> str:
        current = state["current_agent"]
        return "end" if current is None else current

    builder = StateGraph(
        MultiAgentState,
        input_schema=_MultiAgentInput,
        output_schema=MultiAgentState,
    )
    nodes = {
        "Supervisor": supervisor,
        "TopologyAgent": topology_agent,
        "LogAgent": log_agent,
        "DiagnosisAgent": diagnosis_agent,
        "RepairAgent": repair_agent,
        "ReportAgent": report_agent,
    }
    for name, node in nodes.items():
        builder.add_node(
            name,
            node,
            retry_policy=retry_policy,
            error_handler=handle_error,
        )
    builder.add_edge(START, "Supervisor")
    builder.add_conditional_edges(
        "Supervisor",
        route_supervisor,
        {**{agent: node for agent, node in _AGENT_TO_NODE.items()}, "end": END},
    )
    for node in _NODE_TO_AGENT:
        builder.add_edge(node, "Supervisor")
    return builder.compile()


def _terminal_state(state: MultiAgentState, diagnostic: str) -> dict[str, object]:
    return {
        "user_query": state.get("user_query", ""),
        "rewritten_query": state.get("rewritten_query", ""),
        "intent": state.get("intent", "hybrid"),
        "documents": state.get("documents", []),
        "relevance_score": state.get("relevance_score"),
        "grading_feedback": state.get("grading_feedback"),
        "topology_context": state.get("topology_context", {}),
        "metrics": state.get("metrics", {}),
        "answer": state.get("answer") or _FALLBACK_ANSWER,
        "iteration": state.get("iteration", 0),
        "checker_feedback": state.get("checker_feedback"),
        "error": diagnostic,
        "incident_id": state.get("incident_id", "unknown"),
        "task_plan": state.get("task_plan", cast(SupervisorPlan, {})),
        "pending_agents": [],
        "completed_agents": state.get("completed_agents", []),
        "current_agent": None,
        "device_evidence": state.get("device_evidence", {}),
        "log_evidence": state.get("log_evidence", []),
        "diagnosis_result": state.get("diagnosis_result"),
        "repair_plan": state.get("repair_plan"),
        "report": state.get("report", ""),
        "agent_errors": state.get("agent_errors", {}),
        "handoff_count": state.get("handoff_count", 0),
        "diagnosis_iteration": state.get("diagnosis_iteration", 0),
        "report_iteration": state.get("report_iteration", 0),
        "report_feedback": state.get("report_feedback"),
    }


__all__ = ["create_multi_agent_workflow"]
