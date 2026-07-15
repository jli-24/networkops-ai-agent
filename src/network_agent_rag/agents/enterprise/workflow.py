"""Checkpointed enterprise workflow with approval-gated execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast
from typing_extensions import NotRequired, TypedDict
from uuid import uuid4

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, interrupt

from network_agent_rag.agents.diagnosis_workflow import DiagnosisPlan
from network_agent_rag.agents.enterprise.approval import evaluate_risk, validate_approval
from network_agent_rag.agents.enterprise.execution import AllowlistedExecutor, execute_actions
from network_agent_rag.agents.enterprise.state import EnterpriseState, RepairAction
from network_agent_rag.agents.multi_agent.diagnosis_agent import run_diagnosis_agent
from network_agent_rag.agents.multi_agent.log_agent import run_log_agent
from network_agent_rag.agents.multi_agent.repair_agent import run_repair_agent
from network_agent_rag.agents.multi_agent.state import (
    RepairPlan,
    SupervisorPlan,
    completion_update,
)
from network_agent_rag.agents.multi_agent.supervisor import validate_supervisor_plan
from network_agent_rag.agents.multi_agent.topology_agent import run_topology_agent
from network_agent_rag.agents.workflow import DocumentGradeResult
from network_agent_rag.audit import AuditEventType, SQLiteAuditLog


class _EnterpriseInput(TypedDict):
    user_query: str
    incident_id: NotRequired[str]
    session_id: NotRequired[str]


def create_enterprise_workflow(
    *,
    checkpointer: Any,
    plan_incident: Callable[[str, str], SupervisorPlan],
    audit_log: SQLiteAuditLog | None = None,
    retrieve_topology: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_logs: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_metrics: Callable[
        [DiagnosisPlan, dict[str, object]], dict[str, object]
    ]
    | None = None,
    retrieve_documents: Callable[[str], list[Document]] | None = None,
    grade_documents: Callable[[EnterpriseState], DocumentGradeResult] | None = None,
    rewrite_query: Callable[[EnterpriseState], str] | None = None,
    build_repair_plan: Callable[[EnterpriseState], RepairPlan] | None = None,
    plan_actions: Callable[[EnterpriseState], list[RepairAction]] | None = None,
    action_executor: AllowlistedExecutor | None = None,
    clock: Callable[[], datetime] | None = None,
    approval_ttl_seconds: int = 1800,
    max_quality_iterations: int = 3,
    max_retry_attempts: int = 3,
) -> CompiledStateGraph:
    """Compile the v0.3 workflow without changing the v0.2 graph."""

    if approval_ttl_seconds <= 0:
        raise ValueError("approval_ttl_seconds must be positive")
    now = clock or (lambda: datetime.now(timezone.utc))
    retry_policy = RetryPolicy(
        initial_interval=0.0,
        backoff_factor=1.0,
        max_interval=0.0,
        max_attempts=max_retry_attempts,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError),
    )

    def initialize(raw_state: _EnterpriseInput) -> dict[str, object]:
        query = raw_state.get("user_query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("user_query must be a non-empty string")
        incident_id = raw_state.get("incident_id") or uuid4().hex
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise ValueError("incident_id must be a non-empty string")
        incident_id = incident_id.strip()
        plan = plan_incident(query.strip(), incident_id)
        validate_supervisor_plan(plan)
        typed_plan = cast(SupervisorPlan, plan)
        _audit(
            audit_log,
            incident_id=incident_id,
            event_type=AuditEventType.DECISION,
            actor="Supervisor",
            action="plan_incident",
            outcome="succeeded",
            details={"required_agents": list(typed_plan["required_agents"])},
        )
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
            "incident_id": incident_id,
            **(
                {"session_id": raw_state["session_id"]}
                if "session_id" in raw_state
                else {}
            ),
            "task_plan": typed_plan,
            "pending_agents": list(typed_plan["required_agents"]),
            "completed_agents": [],
            "current_agent": None,
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
            "proposed_actions": [],
            "risk_decision": None,
            "approval_result": None,
            "execution_result": None,
            "enterprise_status": "running",
        }

    def topology_agent(state: EnterpriseState) -> dict[str, object]:
        callback = _tool_callback(
            retrieve_topology,
            audit_log,
            state["incident_id"],
            "query_topology",
        )
        return _agent_call(
            audit_log,
            state["incident_id"],
            "TopologyAgent",
            lambda: run_topology_agent(state, callback),
        )

    def log_agent(state: EnterpriseState) -> dict[str, object]:
        callback = _tool_callback(
            retrieve_logs,
            audit_log,
            state["incident_id"],
            "query_logs",
        )
        return _agent_call(
            audit_log,
            state["incident_id"],
            "LogAgent",
            lambda: run_log_agent(state, callback),
        )

    def diagnosis_agent(state: EnterpriseState) -> dict[str, object]:
        metrics_callback = _tool_callback(
            retrieve_metrics,
            audit_log,
            state["incident_id"],
            "query_metrics",
        )
        documents_callback = _tool_callback(
            retrieve_documents,
            audit_log,
            state["incident_id"],
            "search_knowledge",
        )
        return _agent_call(
            audit_log,
            state["incident_id"],
            "DiagnosisAgent",
            lambda: run_diagnosis_agent(
                state,
                retrieve_metrics=metrics_callback,
                retrieve_documents=documents_callback,
                grade_documents=grade_documents,
                rewrite_query=rewrite_query,
                max_quality_iterations=max_quality_iterations,
            ),
        )

    def repair_agent(state: EnterpriseState) -> dict[str, object]:
        def run() -> dict[str, object]:
            update = run_repair_agent(state, build_repair_plan)
            planning_state = cast(EnterpriseState, {**state, **update})
            actions = plan_actions(planning_state) if plan_actions is not None else []
            _validate_actions(actions)
            return {**update, "proposed_actions": actions}

        return _agent_call(
            audit_log,
            state["incident_id"],
            "RepairAgent",
            run,
        )

    def risk_check(state: EnterpriseState) -> dict[str, object]:
        decision = evaluate_risk(
            state,
            now=now(),
            approval_ttl_seconds=approval_ttl_seconds,
        )
        status = "pending_approval" if decision["approval_required"] else "running"
        _audit(
            audit_log,
            incident_id=state["incident_id"],
            event_type=AuditEventType.DECISION,
            actor="RiskCheck",
            action="evaluate_risk",
            outcome="approval_required" if decision["approval_required"] else "proceed",
            details={
                "risk_level": decision["risk_level"],
                "plan_digest": decision["plan_digest"],
            },
        )
        return {"risk_decision": decision, "enterprise_status": status}

    def approval(state: EnterpriseState) -> dict[str, object]:
        risk = state["risk_decision"]
        if risk is None:
            raise ValueError("risk_decision is required for approval")
        _audit(
            audit_log,
            incident_id=state["incident_id"],
            event_type=AuditEventType.APPROVAL,
            actor="system",
            action="request",
            outcome="pending",
            details={"plan_digest": risk["plan_digest"]},
            idempotency_key=f"approval-request:{risk['plan_digest']}",
        )
        payload = interrupt(
            {
                "incident_id": state["incident_id"],
                "risk_level": risk["risk_level"],
                "reasons": risk["reasons"],
                "plan_digest": risk["plan_digest"],
                "expires_at": risk["expires_at"].isoformat(),
                "actions": state["proposed_actions"],
            }
        )
        if not isinstance(payload, dict):
            raise ValueError("approval response must be a dictionary")
        result = validate_approval(state, payload, now=now())
        _audit(
            audit_log,
            incident_id=state["incident_id"],
            event_type=AuditEventType.APPROVAL,
            actor=result["actor"],
            action="decide",
            outcome=result["decision"],
            details={
                "plan_digest": result["plan_digest"],
                "comment": result["comment"],
            },
            idempotency_key=f"approval-result:{result['plan_digest']}",
        )
        return {
            "approval_result": result,
            "enterprise_status": (
                "approved" if result["decision"] == "approve" else "rejected"
            ),
        }

    def execute(state: EnterpriseState) -> dict[str, object]:
        result = execute_actions(state, action_executor)
        actions = {
            action["action_id"]: action for action in state["proposed_actions"]
        }
        for action_result in result["actions"]:
            action = actions.get(action_result["action_id"], {})
            _audit(
                audit_log,
                incident_id=state["incident_id"],
                event_type=AuditEventType.TOOL_CALL,
                actor="Execute",
                action=action_result["action_id"],
                outcome=action_result["status"],
                details={
                    "tool_name": action.get("tool_name"),
                    "target": action.get("target"),
                    "arguments": action.get("arguments", {}),
                    "message": action_result["message"],
                },
            )
        status = "executed" if result["status"] == "succeeded" else "failed"
        error = state.get("error")
        if result["error_code"] is not None:
            error = f"{result['error_code']}: {result['message']}"
        return {
            "execution_result": result,
            "enterprise_status": status,
            "error": error,
        }

    def report_agent(state: EnterpriseState) -> dict[str, object]:
        report = _enterprise_report(state)
        update = completion_update(state, "report")
        _audit(
            audit_log,
            incident_id=state["incident_id"],
            event_type=AuditEventType.AGENT_CALL,
            actor="ReportAgent",
            action="run",
            outcome="succeeded",
        )
        return {**update, "report": report, "answer": report}

    builder = StateGraph(
        EnterpriseState,
        input_schema=_EnterpriseInput,
        output_schema=EnterpriseState,
    )
    builder.add_node("Supervisor", initialize, retry_policy=retry_policy)
    builder.add_node("TopologyAgent", topology_agent, retry_policy=retry_policy)
    builder.add_node("LogAgent", log_agent, retry_policy=retry_policy)
    builder.add_node("DiagnosisAgent", diagnosis_agent, retry_policy=retry_policy)
    builder.add_node("RepairAgent", repair_agent, retry_policy=retry_policy)
    builder.add_node("RiskCheck", risk_check)
    builder.add_node("Approval", approval)
    builder.add_node("Execute", execute)
    builder.add_node("ReportAgent", report_agent)
    builder.add_edge(START, "Supervisor")
    builder.add_conditional_edges(
        "Supervisor", _first_agent, {
            "topology": "TopologyAgent",
            "logs": "LogAgent",
            "diagnosis": "DiagnosisAgent",
        }
    )
    builder.add_conditional_edges(
        "TopologyAgent",
        lambda state: "logs" if "logs" in state["task_plan"]["required_agents"] else "diagnosis",
        {"logs": "LogAgent", "diagnosis": "DiagnosisAgent"},
    )
    builder.add_edge("LogAgent", "DiagnosisAgent")
    builder.add_conditional_edges(
        "DiagnosisAgent",
        _after_diagnosis,
        {"retry": "DiagnosisAgent", "repair": "RepairAgent", "report": "ReportAgent"},
    )
    builder.add_edge("RepairAgent", "RiskCheck")
    builder.add_conditional_edges(
        "RiskCheck",
        lambda state: "approval" if state["risk_decision"]["approval_required"] else "execute",
        {"approval": "Approval", "execute": "Execute"},
    )
    builder.add_conditional_edges(
        "Approval",
        lambda state: "execute" if state["approval_result"]["decision"] == "approve" else "report",
        {"execute": "Execute", "report": "ReportAgent"},
    )
    builder.add_edge("Execute", "ReportAgent")
    builder.add_edge("ReportAgent", END)
    return builder.compile(checkpointer=checkpointer)


def _first_agent(state: EnterpriseState) -> str:
    required = state["task_plan"]["required_agents"]
    if "topology" in required:
        return "topology"
    if "logs" in required:
        return "logs"
    return "diagnosis"


def _after_diagnosis(state: EnterpriseState) -> str:
    if "diagnosis" not in state["completed_agents"]:
        return "retry"
    return "repair" if "repair" in state["task_plan"]["required_agents"] else "report"


def _enterprise_report(state: EnterpriseState) -> str:
    diagnosis = state["diagnosis_result"]
    diagnosis_text = diagnosis["summary"] if diagnosis is not None else "证据不足。"
    approval = state["approval_result"]
    approval_text = "无需审批"
    if approval is not None:
        approval_text = f"{approval['decision']} by {approval['actor']}"
    execution = state["execution_result"]
    if execution is None:
        execution_text = "未执行任何网络变更。"
    else:
        action_messages = "；".join(item["message"] for item in execution["actions"])
        execution_text = f"{execution['status']}: {execution['message']}"
        if action_messages:
            execution_text += f" 结果：{action_messages}"
    return (
        f"## 事件\n\n事件编号：{state['incident_id']}\n\n"
        f"## 诊断\n\n{diagnosis_text}\n\n"
        f"## 风险与审批\n\n风险等级："
        f"{state['repair_plan']['risk_level'] if state['repair_plan'] else 'unknown'}；"
        f"审批：{approval_text}\n\n"
        f"## 执行结果\n\n{execution_text}\n\n"
        "所有执行声明均来自结构化执行器结果。"
    )


def _validate_actions(actions: object) -> None:
    if not isinstance(actions, list):
        raise ValueError("plan_actions must return a list")
    required_strings = ("action_id", "tool_name", "target", "rollback_instructions")
    identifiers: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("repair action must be a dictionary")
        for field in required_strings:
            if not isinstance(action.get(field), str) or not action[field].strip():
                raise ValueError(f"repair action {field} must be non-empty")
        if action["action_id"] in identifiers:
            raise ValueError("repair action_id must be unique")
        identifiers.add(action["action_id"])
        if not isinstance(action.get("arguments"), dict):
            raise ValueError("repair action arguments must be a dictionary")
        checks = action.get("verification_steps")
        if not isinstance(checks, list) or not checks or not all(
            isinstance(item, str) and item.strip() for item in checks
        ):
            raise ValueError("repair action verification_steps must be non-empty")


def _agent_call(
    audit_log: SQLiteAuditLog | None,
    incident_id: str,
    actor: str,
    callback: Callable[[], dict[str, object]],
) -> dict[str, object]:
    try:
        result = callback()
    except Exception as error:
        _audit(
            audit_log,
            incident_id=incident_id,
            event_type=AuditEventType.AGENT_CALL,
            actor=actor,
            action="run",
            outcome="failed",
            details={"error_type": type(error).__name__},
        )
        raise
    _audit(
        audit_log,
        incident_id=incident_id,
        event_type=AuditEventType.AGENT_CALL,
        actor=actor,
        action="run",
        outcome="succeeded",
    )
    return result


def _tool_callback(
    callback: Callable[..., Any] | None,
    audit_log: SQLiteAuditLog | None,
    incident_id: str,
    action: str,
) -> Callable[..., Any] | None:
    if callback is None:
        return None

    def wrapped(*args: object) -> object:
        try:
            result = callback(*args)
        except Exception as error:
            _audit(
                audit_log,
                incident_id=incident_id,
                event_type=AuditEventType.TOOL_CALL,
                actor=action,
                action=action,
                outcome="failed",
                details={"error_type": type(error).__name__},
            )
            raise
        _audit(
            audit_log,
            incident_id=incident_id,
            event_type=AuditEventType.TOOL_CALL,
            actor=action,
            action=action,
            outcome="succeeded",
        )
        return result

    return wrapped


def _audit(
    audit_log: SQLiteAuditLog | None,
    *,
    incident_id: str,
    event_type: AuditEventType,
    actor: str,
    action: str,
    outcome: str,
    details: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> None:
    if audit_log is not None:
        audit_log.record(
            incident_id=incident_id,
            event_type=event_type,
            actor=actor,
            action=action,
            outcome=outcome,
            details=details,
            idempotency_key=idempotency_key,
        )


__all__ = ["create_enterprise_workflow"]
