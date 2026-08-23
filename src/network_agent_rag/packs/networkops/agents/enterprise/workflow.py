"""Checkpointed enterprise workflow with approval-gated execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, cast
from typing_extensions import NotRequired, TypedDict
from uuid import uuid4
import hashlib
import json

from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, interrupt

from network_agent_rag.packs.networkops.agents.diagnosis_workflow import DiagnosisPlan
from network_agent_rag.packs.networkops.agents.enterprise.approval import evaluate_risk, validate_approval
from network_agent_rag.packs.networkops.agents.enterprise.execution import AllowlistedExecutor, execute_actions
from network_agent_rag.packs.networkops.agents.enterprise.state import EnterpriseState, RepairAction
from network_agent_rag.packs.networkops.agents.multi_agent.diagnosis_agent import run_diagnosis_agent
from network_agent_rag.packs.networkops.agents.multi_agent.log_agent import run_log_agent
from network_agent_rag.packs.networkops.agents.multi_agent.repair_agent import run_repair_agent
from network_agent_rag.packs.networkops.agents.multi_agent.state import (
    RepairPlan,
    SupervisorPlan,
    completion_update,
)
from network_agent_rag.packs.networkops.agents.multi_agent.supervisor import validate_supervisor_plan
from network_agent_rag.packs.networkops.agents.multi_agent.topology_agent import run_topology_agent
from network_agent_rag.packs.networkops.agents.workflow import DocumentGradeResult
from network_agent_rag.audit import AuditEventType
from network_agent_rag.auth import (
    AuthorizationError,
    Permission,
    UserContext,
    authorizing_role,
    require_permission,
)
from network_agent_rag.observability import (
    SpanKind,
    SpanStatus,
    TraceCollector,
    TraceEventStatus,
    TraceEventType,
)
from network_agent_rag.policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyEvaluation,
    aggregate_effect,
    evaluate_actions,
)
from network_agent_rag.storage.base import AuditStore, TraceStore


_AUTHORIZE_REPAIR_PLAN = "authorize_repair_plan"
_AUTHORIZE_APPROVAL = "authorize_approval"
_AUTHORIZE_EXECUTION = "authorize_execution"
_AUTHORIZATION_ACTIONS = frozenset(
    {_AUTHORIZE_REPAIR_PLAN, _AUTHORIZE_APPROVAL, _AUTHORIZE_EXECUTION}
)


class _EnterpriseInput(TypedDict):
    user_query: str
    incident_id: NotRequired[str]
    session_id: NotRequired[str]


def create_enterprise_workflow(
    *,
    checkpointer: Any,
    plan_incident: Callable[[str, str], SupervisorPlan],
    audit_log: AuditStore | None = None,
    trace_store: TraceStore | None = None,
    trace_collector: TraceCollector | None = None,
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
    policy_engine: PolicyEngine | None = None,
    clock: Callable[[], datetime] | None = None,
    approval_ttl_seconds: int = 1800,
    max_quality_iterations: int = 3,
    max_retry_attempts: int = 3,
) -> CompiledStateGraph:
    """Compile the v0.3 workflow without changing the v0.2 graph."""

    if approval_ttl_seconds <= 0:
        raise ValueError("approval_ttl_seconds must be positive")
    if policy_engine is not None and audit_log is None:
        raise ValueError("audit_log is required when policy_engine is configured")
    now = clock or (lambda: datetime.now(timezone.utc))
    retry_policy = RetryPolicy(
        initial_interval=0.0,
        backoff_factor=1.0,
        max_interval=0.0,
        max_attempts=max_retry_attempts,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError),
    )

    def initialize(raw_state: _EnterpriseInput, config: RunnableConfig) -> dict[str, object]:
        return _trace_call(
            trace_store,
            config,
            str(raw_state.get("incident_id") or "pending"),
            SpanKind.AGENT,
            "Supervisor",
            lambda _span_id: _initialize(raw_state),
            trace_collector=trace_collector,
        )

    def _initialize(raw_state: _EnterpriseInput) -> dict[str, object]:
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

    def topology_agent(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        return _agent_call(
            audit_log,
            state["incident_id"],
            "TopologyAgent",
            lambda parent_span_id: run_topology_agent(
                state,
                _tool_callback(
                    retrieve_topology,
                    audit_log,
                    state["incident_id"],
                    "query_topology",
                    agent_name="TopologyAgent",
                    trace_store=trace_store,
                    trace_collector=trace_collector,
                    config=config,
                    parent_span_id=parent_span_id,
                ),
            ),
            trace_store=trace_store,
            trace_collector=trace_collector,
            config=config,
        )

    def log_agent(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        return _agent_call(
            audit_log,
            state["incident_id"],
            "LogAgent",
            lambda parent_span_id: run_log_agent(
                state,
                _tool_callback(
                    retrieve_logs,
                    audit_log,
                    state["incident_id"],
                    "query_logs",
                    agent_name="LogAgent",
                    trace_store=trace_store,
                    trace_collector=trace_collector,
                    config=config,
                    parent_span_id=parent_span_id,
                ),
            ),
            trace_store=trace_store,
            trace_collector=trace_collector,
            config=config,
        )

    def diagnosis_agent(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        return _agent_call(
            audit_log,
            state["incident_id"],
            "DiagnosisAgent",
            lambda parent_span_id: run_diagnosis_agent(
                state,
                retrieve_metrics=_tool_callback(
                    retrieve_metrics,
                    audit_log,
                    state["incident_id"],
                    "query_metrics",
                    agent_name="DiagnosisAgent",
                    trace_store=trace_store,
                    trace_collector=trace_collector,
                    config=config,
                    parent_span_id=parent_span_id,
                ),
                retrieve_documents=_tool_callback(
                    retrieve_documents,
                    audit_log,
                    state["incident_id"],
                    "search_knowledge",
                    agent_name="DiagnosisAgent",
                    trace_store=trace_store,
                    trace_collector=trace_collector,
                    config=config,
                    parent_span_id=parent_span_id,
                ),
                grade_documents=grade_documents,
                rewrite_query=rewrite_query,
                max_quality_iterations=max_quality_iterations,
            ),
            trace_store=trace_store,
            trace_collector=trace_collector,
            config=config,
        )

    def repair_agent(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        _authorize(
            config,
            stage="repair_plan",
            permission=Permission.CREATE_REPAIR_PLAN,
            action=_AUTHORIZE_REPAIR_PLAN,
            audit_log=audit_log,
            incident_id=state["incident_id"],
        )

        def run(_parent_span_id: str | None) -> dict[str, object]:
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
            trace_store=trace_store,
            trace_collector=trace_collector,
            config=config,
        )

    def risk_check(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        return _trace_call(
            trace_store,
            config,
            state["incident_id"],
            SpanKind.DECISION,
            "RiskCheck",
            lambda _span_id: _risk_check(state, config),
            trace_collector=trace_collector,
        )

    def _risk_check(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        decision = evaluate_risk(
            state,
            now=now(),
            approval_ttl_seconds=approval_ttl_seconds,
        )
        if policy_engine is not None:
            context = _policy_execution_context(config)
            evaluations = evaluate_actions(
                policy_engine,
                context,
                state["proposed_actions"],
                risk_level=decision["risk_level"],
                timestamp=now(),
            )
            effect = aggregate_effect(evaluations)
            _audit_policy_evaluations(
                audit_log,
                state["incident_id"],
                decision["plan_digest"],
                context,
                evaluations,
            )
            reasons = list(decision["reasons"])
            if effect == PolicyEffect.DENY:
                decision = {
                    **decision,
                    "approval_required": False,
                    "reasons": [*reasons, "policy denied execution"],
                }
            elif effect == PolicyEffect.REQUIRE_APPROVAL:
                decision = {
                    **decision,
                    "approval_required": True,
                    "reasons": [*reasons, "policy requires approval"],
                }
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

    def approval(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        context = _collector_context(config)
        configurable = config.get("configurable", {})
        is_resume = isinstance(configurable, dict) and configurable.get("is_resume") == "true"
        if trace_collector is not None and context is not None and not is_resume:
            risk = state.get("risk_decision") or {}
            trace_collector.approval_pause(
                incident_id=state["incident_id"],
                agent_name="Approval",
                node_name="Approval",
                input_data={"risk_level": risk.get("risk_level")},
                attempt=1,
                **context,
            )
        return _trace_call(
            trace_store,
            config,
            state["incident_id"],
            SpanKind.APPROVAL,
            "Approval",
            lambda _span_id: _approval(state, config),
            trace_collector=trace_collector,
        )

    def _approval(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
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
        _authorize(
            config,
            stage="approval",
            permission=Permission.APPROVE_REPAIR,
            action=_AUTHORIZE_APPROVAL,
            audit_log=audit_log,
            incident_id=state["incident_id"],
        )
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

    def execute(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        _authorize(
            config,
            stage="execution",
            permission=Permission.EXECUTE_REPAIR,
            action=_AUTHORIZE_EXECUTION,
            audit_log=audit_log,
            incident_id=state["incident_id"],
        )
        if policy_engine is not None:
            context = _policy_execution_context(config)
            risk = state.get("risk_decision")
            if not isinstance(risk, dict):
                raise ValueError("risk_decision is required for policy evaluation")
            evaluations = evaluate_actions(
                policy_engine,
                context,
                state["proposed_actions"],
                risk_level=risk["risk_level"],
                timestamp=now(),
            )
            effect = aggregate_effect(evaluations)
            _audit_policy_evaluations(
                audit_log,
                state["incident_id"],
                risk["plan_digest"],
                context,
                evaluations,
            )
            if effect == PolicyEffect.DENY:
                return _policy_blocked(
                    state,
                    "POLICY_DENIED",
                    "Policy denied the proposed execution; no action was executed.",
                )
            approval = state.get("approval_result")
            if effect == PolicyEffect.REQUIRE_APPROVAL and (
                not isinstance(approval, dict)
                or approval.get("decision") != "approve"
            ):
                return _policy_blocked(
                    state,
                    "POLICY_APPROVAL_REQUIRED",
                    "Policy requires existing human approval before execution.",
                )
        context = _collector_context(config)
        if trace_collector is not None and context is not None:
            actions = state["proposed_actions"]
            first = actions[0] if actions else {}
            trace_collector.repair_execute(
                incident_id=state["incident_id"],
                agent_name="Execute",
                node_name="Execute",
                input_data={
                    "action_count": len(actions),
                    "action_id": first.get("action_id"),
                    "tool_name": first.get("tool_name"),
                    "target": first.get("target"),
                },
                attempt=1,
                **context,
            )
        return _trace_call(
            trace_store,
            config,
            state["incident_id"],
            SpanKind.EXECUTION,
            "Execute",
            lambda _span_id: _execute(state),
            trace_collector=trace_collector,
        )

    def _execute(state: EnterpriseState) -> dict[str, object]:
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

    def report_agent(
        state: EnterpriseState, config: RunnableConfig
    ) -> dict[str, object]:
        return _agent_call(
            audit_log,
            state["incident_id"],
            "ReportAgent",
            lambda _span_id: _report_agent(state),
            trace_store=trace_store,
            trace_collector=trace_collector,
            config=config,
        )

    def _report_agent(state: EnterpriseState) -> dict[str, object]:
        report = _enterprise_report(state)
        update = completion_update(state, "report")
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
    audit_log: AuditStore | None,
    incident_id: str,
    actor: str,
    callback: Callable[[str | None], dict[str, object]],
    *,
    trace_store: TraceStore | None = None,
    trace_collector: TraceCollector | None = None,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    def run(span_id: str | None) -> dict[str, object]:
        try:
            result = callback(span_id)
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

    return _trace_call(
        trace_store,
        config or {},
        incident_id,
        SpanKind.AGENT,
        actor,
        run,
        trace_collector=trace_collector,
    )


def _tool_callback(
    callback: Callable[..., Any] | None,
    audit_log: AuditStore | None,
    incident_id: str,
    action: str,
    *,
    agent_name: str,
    trace_store: TraceStore | None = None,
    trace_collector: TraceCollector | None = None,
    config: RunnableConfig | None = None,
    parent_span_id: str | None = None,
) -> Callable[..., Any] | None:
    if callback is None:
        return None

    def wrapped(*args: object) -> object:
        span = _start_trace_span(
            trace_store,
            config or {},
            incident_id,
            SpanKind.TOOL,
            action,
            parent_span_id=parent_span_id,
        )
        try:
            if trace_collector is not None and _collector_context(config or {}) is not None:
                context = _collector_context(config or {})
                result = trace_collector.record_call(
                    incident_id=incident_id,
                    agent_name=agent_name,
                    node_name=action,
                    event_type=(
                        TraceEventType.RAG_RETRIEVAL
                        if action == "search_knowledge"
                        else TraceEventType.TOOL_CALL
                    ),
                    operation=lambda: callback(*args),
                    input_data={"argument_count": len(args)},
                    output_builder=lambda value: _tool_result_summary(action, value),
                    attempt=span.attempt if span is not None else None,
                    **context,
                )
            else:
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
            _finish_trace_span(trace_store, span, SpanStatus.FAILED, error)
            raise
        _audit(
            audit_log,
            incident_id=incident_id,
            event_type=AuditEventType.TOOL_CALL,
            actor=action,
            action=action,
            outcome="succeeded",
        )
        _finish_trace_span(trace_store, span, SpanStatus.SUCCEEDED)
        return result

    return wrapped


def _trace_call(
    trace_store: TraceStore | None,
    config: RunnableConfig,
    incident_id: str,
    kind: SpanKind,
    name: str,
    callback: Callable[[str | None], dict[str, object]],
    *,
    trace_collector: TraceCollector | None = None,
) -> dict[str, object]:
    span = _start_trace_span(trace_store, config, incident_id, kind, name)
    collector_token = None
    context = _collector_context(config)
    if (
        trace_collector is not None
        and context is not None
        and kind in {SpanKind.AGENT, SpanKind.DECISION}
    ):
        collector_token = trace_collector.agent_enter(
            incident_id=incident_id,
            agent_name=name,
            node_name=name,
            input_data={"attempt": span.attempt if span is not None else 1},
            attempt=span.attempt if span is not None else None,
            **context,
        )
    try:
        result = callback(span.span_id if span is not None else None)
    except BaseException as error:
        status = (
            SpanStatus.INTERRUPTED
            if type(error).__name__ in {"GraphInterrupt", "GraphBubbleUp"}
            else SpanStatus.FAILED
        )
        _finish_trace_span(trace_store, span, status, error)
        if collector_token is not None:
            trace_collector.agent_exit(
                collector_token,
                status=(
                    TraceEventStatus.INTERRUPTED
                    if status == SpanStatus.INTERRUPTED
                    else TraceEventStatus.FAILED
                ),
                output_data={"error_type": type(error).__name__},
            )
        raise
    _finish_trace_span(trace_store, span, SpanStatus.SUCCEEDED, attributes=_result_summary(result))
    if collector_token is not None:
        trace_collector.agent_exit(
            collector_token,
            status=TraceEventStatus.SUCCEEDED,
            output_data=_result_summary(result),
        )
    return result


def _start_trace_span(
    trace_store: TraceStore | None,
    config: RunnableConfig,
    incident_id: str,
    kind: SpanKind,
    name: str,
    *,
    parent_span_id: str | None = None,
):
    if trace_store is None:
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    trace_id = configurable.get("trace_id")
    run_id = configurable.get("run_id")
    parent = parent_span_id or configurable.get("workflow_span_id")
    if not all(isinstance(item, str) and item for item in (trace_id, run_id)):
        return None
    return trace_store.start_span(
        trace_id=trace_id,
        run_id=run_id,
        incident_id=incident_id,
        parent_span_id=parent if isinstance(parent, str) else None,
        kind=kind,
        name=name,
        input_summary_hash=_summary_digest(
            {"incident_id": incident_id, "kind": kind.value, "name": name}
        ),
    )


def _finish_trace_span(
    trace_store: TraceStore | None,
    span: object,
    status: SpanStatus,
    error: BaseException | None = None,
    *,
    attributes: dict[str, object] | None = None,
) -> None:
    if trace_store is None or span is None:
        return
    trace_store.finish_span(
        span.span_id,
        status=status,
        error_code=type(error).__name__ if error is not None else None,
        attributes=attributes,
        output_summary_hash=_summary_digest(
            {
                "status": status.value,
                "error_code": type(error).__name__ if error is not None else None,
                "attributes": attributes or {},
            }
        ),
    )


def _summary_digest(summary: dict[str, object]) -> str:
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collector_context(config: RunnableConfig) -> dict[str, str] | None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    trace_id = configurable.get("trace_id")
    run_id = configurable.get("run_id")
    if not all(isinstance(item, str) and item for item in (trace_id, run_id)):
        return None
    return {"trace_id": trace_id, "run_id": run_id}


def _authorize(
    config: RunnableConfig,
    *,
    stage: str,
    permission: Permission,
    action: str,
    audit_log: AuditStore | None,
    incident_id: str,
) -> None:
    contexts = _rbac_contexts(config)
    if contexts is not None and audit_log is None:
        raise RuntimeError("audit_log is required for explicit RBAC mode")
    if contexts is None and not _incident_uses_rbac(audit_log, incident_id):
        return
    context = contexts.get(stage) if contexts is not None else None
    if context is None:
        _audit_authorization(
            audit_log,
            incident_id=incident_id,
            action=action,
            outcome="denied",
            actor_id="anonymous",
            actor_role=None,
            permission=permission,
        )
        raise AuthorizationError("anonymous", permission)

    role = authorizing_role(context, permission)
    try:
        require_permission(context, permission)
    except AuthorizationError:
        _audit_authorization(
            audit_log,
            incident_id=incident_id,
            action=action,
            outcome="denied",
            actor_id=context.user.user_id,
            actor_role=context.user.roles[0].value,
            permission=permission,
        )
        raise
    _audit_authorization(
        audit_log,
        incident_id=incident_id,
        action=action,
        outcome="allowed",
        actor_id=context.user.user_id,
        actor_role=role.value if role is not None else None,
        permission=permission,
    )


def _rbac_contexts(
    config: RunnableConfig,
) -> Mapping[str, UserContext] | None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping) or "rbac_contexts" not in configurable:
        return None
    contexts = configurable["rbac_contexts"]
    if not isinstance(contexts, Mapping):
        raise TypeError("rbac_contexts must be a mapping of phase names to UserContext")
    if not all(
        isinstance(key, str) and isinstance(value, UserContext)
        for key, value in contexts.items()
    ):
        raise TypeError("rbac_contexts must be a mapping of phase names to UserContext")
    return contexts


def _policy_execution_context(config: RunnableConfig) -> UserContext:
    contexts = _rbac_contexts(config)
    context = contexts.get("execution") if contexts is not None else None
    if context is None:
        raise AuthorizationError("anonymous", Permission.EXECUTE_REPAIR)
    require_permission(context, Permission.EXECUTE_REPAIR)
    return context


def _audit_policy_evaluations(
    audit_log: AuditStore | None,
    incident_id: str,
    plan_digest: str,
    context: UserContext,
    evaluations: tuple[PolicyEvaluation, ...],
) -> None:
    for evaluation in evaluations:
        decision = evaluation.decision.decision
        details = {
            "policy_id": evaluation.decision.decision_id,
            "decision": decision.value,
            "operation": evaluation.context.operation.value,
            "risk_level": evaluation.context.risk_level.value,
        }
        key = f"policy:{plan_digest}:{evaluation.action_id}"
        _audit(
            audit_log,
            incident_id=incident_id,
            event_type=AuditEventType.DECISION,
            actor=context.user.user_id,
            action="policy_evaluation",
            outcome="evaluated",
            details=details,
            idempotency_key=f"{key}:evaluation",
        )
        if decision == PolicyEffect.DENY:
            _audit(
                audit_log,
                incident_id=incident_id,
                event_type=AuditEventType.DECISION,
                actor=context.user.user_id,
                action="policy_denied",
                outcome="denied",
                details=details,
                idempotency_key=f"{key}:denied",
            )
        elif decision == PolicyEffect.REQUIRE_APPROVAL:
            _audit(
                audit_log,
                incident_id=incident_id,
                event_type=AuditEventType.DECISION,
                actor=context.user.user_id,
                action="policy_approval_required",
                outcome="approval_required",
                details=details,
                idempotency_key=f"{key}:approval",
            )


def _policy_blocked(
    state: EnterpriseState,
    error_code: str,
    message: str,
) -> dict[str, object]:
    result = {
        "status": "blocked",
        "actions": [],
        "error_code": error_code,
        "message": message,
    }
    return {
        "execution_result": result,
        "enterprise_status": "failed",
        "error": f"{error_code}: {message}",
    }


def _incident_uses_rbac(
    audit_log: AuditStore | None,
    incident_id: str,
) -> bool:
    if audit_log is None:
        return False
    return any(
        event.event_type == AuditEventType.DECISION
        and event.actor == "authorization"
        and event.action in _AUTHORIZATION_ACTIONS
        for event in audit_log.list_events(incident_id)
    )


def _audit_authorization(
    audit_log: AuditStore | None,
    *,
    incident_id: str,
    action: str,
    outcome: str,
    actor_id: str,
    actor_role: str | None,
    permission: Permission,
) -> None:
    _audit(
        audit_log,
        incident_id=incident_id,
        event_type=AuditEventType.DECISION,
        actor="authorization",
        action=action,
        outcome=outcome,
        details={
            "actor_id": actor_id,
            "actor_role": actor_role,
            "permission": permission.value,
        },
    )


def _tool_result_summary(action: str, result: object) -> dict[str, object]:
    if action == "search_knowledge":
        return {"document_count": len(result) if isinstance(result, list) else 0}
    summary: dict[str, object] = {}
    if isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, str):
            summary["status"] = status
        for field in ("records", "interfaces", "alarms", "items"):
            items = result.get(field)
            if isinstance(items, list):
                summary["result_count"] = len(items)
                break
    return summary


def _result_summary(result: dict[str, object]) -> dict[str, object]:
    keys = ("enterprise_status", "relevance_score", "diagnosis_iteration")
    summary = {
        key: result[key]
        for key in keys
        if key in result and isinstance(result[key], (str, int, float, bool, type(None)))
    }
    risk = result.get("risk_decision")
    if isinstance(risk, dict) and isinstance(risk.get("risk_level"), str):
        summary["risk_level"] = risk["risk_level"]
    approval = result.get("approval_result")
    if isinstance(approval, dict) and isinstance(approval.get("decision"), str):
        summary["approval_decision"] = approval["decision"]
    execution = result.get("execution_result")
    if isinstance(execution, dict) and isinstance(execution.get("status"), str):
        summary["execution_status"] = execution["status"]
    diagnosis = result.get("diagnosis_result")
    if isinstance(diagnosis, dict) and isinstance(diagnosis.get("evidence_refs"), list):
        summary["evidence_count"] = len(diagnosis["evidence_refs"])
    if result.get("error"):
        summary["has_error"] = True
    return summary


def _audit(
    audit_log: AuditStore | None,
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
