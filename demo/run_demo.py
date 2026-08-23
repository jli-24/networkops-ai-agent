"""Run the deterministic NetworkOps fault scenario end to end."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import TextIO, cast
from uuid import uuid4

from langchain_core.documents import Document
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from network_agent_rag.packs.networkops.agents.enterprise import (
    AllowlistedExecutor,
    create_enterprise_workflow,
)
from network_agent_rag.packs.networkops.agents.enterprise.state import RepairAction
from network_agent_rag.packs.networkops.agents.multi_agent.state import RepairPlan, SupervisorPlan
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.auth import Role, User, UserContext
from network_agent_rag.core.config import Settings
from network_agent_rag.evaluation import (
    BenchmarkCase,
    BenchmarkObservation,
    BenchmarkResultStore,
    BenchmarkRunner,
)
from network_agent_rag.observability import (
    SQLiteTraceStore,
    SpanKind,
    SpanStatus,
    TraceCollector,
)
from network_agent_rag.policy import PolicyEffect, PolicyEngine, PolicyOperation
from network_agent_rag.policy.registry import PolicyRegistry
from network_agent_rag.policy.rules import default_policy_registry

from demo.fault_scenarios import (
    DEMO_TIME,
    INCIDENT_ID,
    LinkFailureScenario,
    build_link_failure_scenario,
)


TRACE_ID = "trace-INC-DEMO-001"
EVALUATION_CASE_ID = "NET-DEMO-001"
ACTION_ID = "DEMO-ACTION-001"
TOOL_NAME = "network.restart_interface"


@dataclass(frozen=True, slots=True)
class DemoPaths:
    checkpoint_db: Path
    audit_db: Path
    trace_db: Path
    evaluation_dir: Path


@dataclass(frozen=True, slots=True)
class DemoIncident:
    incident_id: str
    status: str
    risk: str
    description: str
    created_time: datetime


@dataclass(frozen=True, slots=True)
class DemoResult:
    incident: DemoIncident
    diagnosis: str
    root_cause: str
    repair_plan: str
    repair_operation: str
    policy_operation: str
    policy_decision: str
    approval: str
    execution: str
    verification: str
    evaluation_case_id: str
    top1_root_cause: str
    evaluation_time_ms: float
    paths: DemoPaths


def run_demo(
    *,
    base_directory: str | Path | None = None,
    output: TextIO | None = None,
) -> DemoResult:
    """Execute the local simulation and persist Console-readable projections."""

    return asyncio.run(
        _run_demo(
            paths=_resolve_paths(base_directory),
            output=output or sys.stdout,
        )
    )


async def _run_demo(*, paths: DemoPaths, output: TextIO) -> DemoResult:
    scenario = build_link_failure_scenario()
    audit = SQLiteAuditLog(paths.audit_db)
    trace = SQLiteTraceStore(paths.trace_db)
    collector = TraceCollector(audit, clock=lambda: DEMO_TIME)
    policy_engine = _demo_policy_engine()
    invocation_id = uuid4().hex[:12]
    initial_run_id = f"run-{INCIDENT_ID}-{invocation_id}-initial"
    resume_run_id = f"run-{INCIDENT_ID}-{invocation_id}-resume"
    engineer = UserContext(
        user=User(
            user_id="demo-engineer",
            username="demo-engineer",
            roles=(Role.ENGINEER,),
        )
    )
    admin = UserContext(
        user=User(
            user_id="demo-admin",
            username="demo-admin",
            roles=(Role.ADMIN,),
        )
    )

    paths.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(paths.checkpoint_db)) as saver:
        await saver.setup()
        await saver.adelete_thread(INCIDENT_ID)
        graph = create_enterprise_workflow(
            checkpointer=saver,
            plan_incident=lambda query, incident_id: _incident_plan(),
            audit_log=audit,
            trace_store=trace,
            trace_collector=collector,
            retrieve_topology=lambda plan: _topology_evidence(),
            retrieve_logs=lambda plan: _log_evidence(),
            retrieve_metrics=lambda plan, topology: _metric_evidence(),
            retrieve_documents=lambda query: _knowledge_evidence(),
            build_repair_plan=lambda state: _repair_plan(),
            plan_actions=lambda state: _repair_actions(),
            action_executor=AllowlistedExecutor(
                {TOOL_NAME: lambda action: _execute_repair(scenario, action)}
            ),
            policy_engine=policy_engine,
            clock=lambda: DEMO_TIME,
        )

        first_workflow = trace.start_span(
            trace_id=TRACE_ID,
            run_id=initial_run_id,
            incident_id=INCIDENT_ID,
            kind=SpanKind.WORKFLOW,
            name="EnterpriseWorkflow",
            attributes={"risk_level": "high", "phase": "diagnosis"},
            idempotency_key=f"demo:{invocation_id}:workflow:initial",
        )
        initial_config = _config(
            run_id=initial_run_id,
            workflow_span_id=first_workflow.span_id,
            engineer=engineer,
            admin=admin,
        )
        first = await graph.ainvoke(
            {
                "user_query": scenario.description,
                "incident_id": scenario.incident_id,
            },
            initial_config,
        )
        if "__interrupt__" not in first:
            trace.finish_span(first_workflow.span_id, status=SpanStatus.FAILED)
            raise RuntimeError("demo workflow did not pause for approval")
        snapshot = await graph.aget_state(initial_config)
        risk = snapshot.values.get("risk_decision")
        if not isinstance(risk, dict) or not isinstance(risk.get("plan_digest"), str):
            trace.finish_span(first_workflow.span_id, status=SpanStatus.FAILED)
            raise RuntimeError("demo workflow did not produce a risk decision")
        _record_policy_span(
            trace,
            first_workflow.span_id,
            run_id=initial_run_id,
            invocation_id=invocation_id,
        )
        trace.finish_span(
            first_workflow.span_id,
            status=SpanStatus.INTERRUPTED,
            attributes={"risk_level": "high", "execution_status": "not_executed"},
        )

        resumed_workflow = trace.start_span(
            trace_id=TRACE_ID,
            run_id=resume_run_id,
            incident_id=INCIDENT_ID,
            kind=SpanKind.WORKFLOW,
            name="EnterpriseWorkflow",
            attributes={"risk_level": "high", "phase": "approval_resume"},
            idempotency_key=f"demo:{invocation_id}:workflow:resume",
        )
        resume_config = _config(
            run_id=resume_run_id,
            workflow_span_id=resumed_workflow.span_id,
            engineer=engineer,
            admin=admin,
            is_resume=True,
        )
        collector.resume(
            incident_id=INCIDENT_ID,
            trace_id=TRACE_ID,
            run_id=resume_run_id,
            agent_name="Approval",
            node_name="Approval",
            input_data={"decision": "approve"},
            attempt=1,
        )
        final = await graph.ainvoke(
            Command(
                resume={
                    "decision": "approve",
                    "actor": "demo-admin",
                    "plan_digest": risk["plan_digest"],
                    "comment": "Approved for the deterministic demo.",
                }
            ),
            resume_config,
        )
        execution = final.get("execution_result")
        succeeded = isinstance(execution, dict) and execution.get("status") == "succeeded"
        trace.finish_span(
            resumed_workflow.span_id,
            status=SpanStatus.SUCCEEDED if succeeded else SpanStatus.FAILED,
            error_code=None if succeeded else "DEMO_EXECUTION_FAILED",
            attributes={
                "risk_level": "high",
                "execution_status": "succeeded" if succeeded else "failed",
            },
        )
        if not succeeded:
            raise RuntimeError("demo repair execution failed")

    verification = _verify_repair(scenario)
    if not verification:
        raise RuntimeError("demo verification failed")
    evaluation_time, evaluated_root_cause = _save_evaluation(
        paths.evaluation_dir,
        final=cast(dict[str, object], final),
        audit=audit,
        trace=trace,
        run_ids=(initial_run_id, resume_run_id),
    )
    result = DemoResult(
        incident=DemoIncident(
            incident_id=INCIDENT_ID,
            status="succeeded",
            risk="high",
            description=scenario.description,
            created_time=scenario.created_at,
        ),
        diagnosis="Interface failure",
        root_cause="SW-01 uplink failure",
        repair_plan="Enable interface Gi0/1",
        repair_operation="restart_interface",
        policy_operation=PolicyOperation.RESTART_DEVICE.value,
        policy_decision=PolicyEffect.REQUIRE_APPROVAL.value,
        approval="APPROVED",
        execution="SUCCESS",
        verification="PASSED",
        evaluation_case_id=EVALUATION_CASE_ID,
        top1_root_cause=evaluated_root_cause,
        evaluation_time_ms=evaluation_time,
        paths=paths,
    )
    _render(result, output)
    return result


def _resolve_paths(base_directory: str | Path | None) -> DemoPaths:
    if base_directory is not None:
        base = Path(base_directory)
        return DemoPaths(
            checkpoint_db=base / "checkpoints.sqlite3",
            audit_db=base / "audit.sqlite3",
            trace_db=base / "observability.sqlite3",
            evaluation_dir=base / "evaluations",
        )
    settings = Settings()
    return DemoPaths(
        checkpoint_db=Path(settings.checkpoint_db_path),
        audit_db=Path(settings.audit_db_path),
        trace_db=Path(settings.observability_db_path),
        evaluation_dir=Path(settings.benchmark_results_path),
    )


def _incident_plan() -> SupervisorPlan:
    return cast(
        SupervisorPlan,
        {
            "analysis": {
                "devices": ["SW-01", "SERVER-01"],
                "interfaces": ["SW-01:Gi0/1", "SERVER-01:eth0"],
                "symptom": "service unreachable after link down",
                "start_time": "2026-01-01T07:55:00+00:00",
                "end_time": "2026-01-01T08:05:00+00:00",
                "required_sources": ["topology", "monitoring", "logs", "knowledge"],
            },
            "required_agents": ["topology", "logs", "diagnosis", "repair", "report"],
            "requires_repair": True,
        },
    )


def _topology_evidence() -> dict[str, object]:
    return {
        "path": ["SW-01", "SERVER-01"],
        "links": [
            {
                "source": "SW-01",
                "target": "SERVER-01",
                "type": "connect",
                "source_interface": "Gi0/1",
                "target_interface": "eth0",
                "evidence_ref": "TOPO-DEMO-001",
            }
        ],
    }


def _log_evidence() -> dict[str, object]:
    return {
        "ok": True,
        "records": [
            {
                "source_id": "SW-01",
                "interface_name": "Gi0/1",
                "timestamp": "2026-01-01T08:00:00+00:00",
                "event_type": "LINK_DOWN",
                "message": "Interface Gi0/1 changed state to down.",
                "evidence_ref": "LOG-DEMO-001",
            }
        ],
    }


def _metric_evidence() -> dict[str, object]:
    return {
        "status": "down",
        "server_reachable": False,
        "interfaces": [
            {
                "device_id": "SW-01",
                "interface_name": "Gi0/1",
                "oper_status": "down",
                "packet_loss": 100.0,
                "evidence_ref": "METRIC-DEMO-001",
            }
        ],
    }


def _knowledge_evidence() -> list[Document]:
    return [
        Document(
            page_content=(
                "A LINK_DOWN alarm with 100 percent packet loss indicates a failed "
                "uplink. Verify administrative state and restore the interface only "
                "after approval."
            ),
            metadata={
                "relevance_score": 0.96,
                "evidence_ref": "KB-DEMO-001",
                "document_id": "KB-DEMO-001",
            },
        )
    ]


def _repair_plan() -> RepairPlan:
    return {
        "risk_level": "high",
        "requires_human_approval": True,
        "steps": ["Enable SW-01 interface Gi0/1 after approval."],
        "verification_steps": [
            "Verify Gi0/1 is up and packet loss to SERVER-01 is zero."
        ],
        "rollback_conditions": [
            "Restore the previous interface state if link stability degrades."
        ],
        "execution_status": "not_executed",
    }


def _repair_actions() -> list[RepairAction]:
    return [
        {
            "action_id": ACTION_ID,
            "tool_name": TOOL_NAME,
            "target": "SW-01:Gi0/1",
            "arguments": {"admin_status": "up"},
            "verification_steps": ["Verify link status and packet loss."],
            "rollback_instructions": "Restore the previous interface state.",
        }
    ]


def _execute_repair(
    scenario: LinkFailureScenario,
    action: RepairAction,
) -> dict[str, object]:
    if action["action_id"] != ACTION_ID:
        return {"status": "failed", "message": "Unknown demo action."}
    scenario.simulator.update_link_state(
        "SW-01",
        "SERVER-01",
        status="up",
        packet_loss=0.0,
    )
    return {"status": "succeeded", "message": "SW-01 Gi0/1 enabled."}


def _verify_repair(scenario: LinkFailureScenario) -> bool:
    link = scenario.simulator.twin.get_link_state("SW-01", "SERVER-01")
    return link.status == "up" and link.packet_loss == 0.0


def _demo_policy_engine() -> PolicyEngine:
    base = default_policy_registry()
    operations = dict(base.tool_operations)
    operations[TOOL_NAME] = PolicyOperation.RESTART_DEVICE
    return PolicyEngine(PolicyRegistry(base.rules, operations))


def _config(
    *,
    run_id: str,
    workflow_span_id: str,
    engineer: UserContext,
    admin: UserContext,
    is_resume: bool = False,
) -> dict[str, object]:
    configurable: dict[str, object] = {
        "thread_id": INCIDENT_ID,
        "trace_id": TRACE_ID,
        "run_id": run_id,
        "workflow_span_id": workflow_span_id,
        "rbac_contexts": {
            "repair_plan": engineer,
            "approval": admin,
            "execution": engineer,
        },
    }
    if is_resume:
        configurable["is_resume"] = "true"
    return {"configurable": configurable}


def _record_policy_span(
    trace: SQLiteTraceStore,
    parent_span_id: str,
    *,
    run_id: str,
    invocation_id: str,
) -> None:
    span = trace.start_span(
        trace_id=TRACE_ID,
        run_id=run_id,
        incident_id=INCIDENT_ID,
        parent_span_id=parent_span_id,
        kind=SpanKind.DECISION,
        name="PolicyEngine",
        attributes={
            "risk_level": "high",
            "decision": PolicyEffect.REQUIRE_APPROVAL.value,
            "operation": PolicyOperation.RESTART_DEVICE.value,
        },
        idempotency_key=f"demo:{invocation_id}:policy-engine",
    )
    trace.finish_span(
        span.span_id,
        status=SpanStatus.SUCCEEDED,
        attributes={"decision": PolicyEffect.REQUIRE_APPROVAL.value},
    )


def _save_evaluation(
    directory: Path,
    *,
    final: dict[str, object],
    audit: SQLiteAuditLog,
    trace: SQLiteTraceStore,
    run_ids: tuple[str, str],
) -> tuple[float, str]:
    spans = [
        span
        for run_id in run_ids
        for span in trace.list_spans(INCIDENT_ID, run_id=run_id)
    ]
    route = list(
        dict.fromkeys(
            span.name
            for span in spans
            if span.kind
            in {
                SpanKind.AGENT,
                SpanKind.TOOL,
                SpanKind.DECISION,
                SpanKind.APPROVAL,
                SpanKind.EXECUTION,
            }
        )
    )
    evidence_refs = _evaluation_evidence_refs(final)
    root_cause, confidence = _evaluate_root_cause(final, evidence_refs)
    workflow_duration = round(
        sum(
            span.duration_ms or 0.0
            for span in spans
            if span.kind == SpanKind.WORKFLOW
        ),
        3,
    )
    risk = final.get("risk_decision")
    approval = final.get("approval_result")
    execution = final.get("execution_result")
    approval_required = isinstance(risk, dict) and risk.get("approval_required") is True
    execution_status = (
        str(execution.get("status"))
        if isinstance(execution, dict)
        else "not_executed"
    )
    actions = {event.action: event for event in audit.list_events(INCIDENT_ID)}
    safe_execution = (
        actions.get("authorize_execution") is not None
        and actions["authorize_execution"].outcome == "allowed"
        and actions.get("policy_approval_required") is not None
        and isinstance(approval, dict)
        and approval.get("decision") == "approve"
    )
    case = BenchmarkCase(
        case_id=EVALUATION_CASE_ID,
        query="Diagnose the SW-01 to SERVER-01 outage.",
        expected_route=[
            "Supervisor",
            "TopologyAgent",
            "query_topology",
            "LogAgent",
            "query_logs",
            "DiagnosisAgent",
            "query_metrics",
            "search_knowledge",
            "RepairAgent",
            "RiskCheck",
            "Approval",
            "PolicyEngine",
            "Execute",
            "ReportAgent",
        ],
        required_evidence_refs=[
            "TOPO-DEMO-001",
            "LOG-DEMO-001",
            "METRIC-DEMO-001",
            "KB-DEMO-001",
        ],
        expected_root_cause="SW-01 uplink failure",
        confidence_min=90.0,
        confidence_max=100.0,
        approval_required=True,
        expected_execution_status="succeeded",
    )
    observation = BenchmarkObservation(
        route=route,
        evidence_refs=evidence_refs,
        root_cause=root_cause,
        confidence_percent=confidence,
        approval_required=approval_required,
        execution_status=execution_status,
        grounded_claims=len(set(evidence_refs) & set(case.required_evidence_refs)),
        total_claims=len(case.required_evidence_refs),
        unsafe_execution_claims=0 if safe_execution else 1,
        duration_ms=workflow_duration,
        quality_iterations=int(final.get("diagnosis_iteration", 0)),
    )
    runner = BenchmarkRunner(
        lambda _: observation,
        clock=lambda: DEMO_TIME,
        run_id_factory=lambda: "demo-INC-DEMO-001",
    )
    result = runner.run(
        [case],
        dataset_name="network-fault-demo",
        dataset_version="v1",
    )
    BenchmarkResultStore(directory).save(result)
    return workflow_duration, root_cause


def _evaluation_evidence_refs(final: dict[str, object]) -> list[str]:
    references: list[str] = []
    diagnosis = final.get("diagnosis_result")
    if isinstance(diagnosis, dict) and isinstance(diagnosis.get("evidence_refs"), list):
        references.extend(
            str(reference)
            for reference in diagnosis["evidence_refs"]
            if isinstance(reference, str)
        )
    documents = final.get("documents")
    if isinstance(documents, list):
        for document in documents:
            if isinstance(document, Document):
                reference = document.metadata.get("evidence_ref")
                if isinstance(reference, str):
                    references.append(reference)
    return list(dict.fromkeys(references))


def _evaluate_root_cause(
    final: dict[str, object],
    evidence_refs: list[str],
) -> tuple[str, float]:
    metrics = final.get("metrics")
    interfaces = metrics.get("interfaces") if isinstance(metrics, dict) else None
    interface_down = isinstance(interfaces, list) and any(
        isinstance(item, dict)
        and item.get("device_id") == "SW-01"
        and item.get("interface_name") == "Gi0/1"
        and item.get("oper_status") == "down"
        for item in interfaces
    )
    total_loss = isinstance(interfaces, list) and any(
        isinstance(item, dict) and item.get("packet_loss") == 100.0
        for item in interfaces
    )
    logs = final.get("log_evidence")
    link_alarm = isinstance(logs, list) and any(
        isinstance(record, dict) and record.get("event_type") == "LINK_DOWN"
        for record in logs
    )
    topology = final.get("topology_context")
    path_matches = (
        isinstance(topology, dict)
        and topology.get("path") == ["SW-01", "SERVER-01"]
    )
    knowledge_found = "KB-DEMO-001" in evidence_refs
    checks = (interface_down, total_loss, link_alarm, path_matches, knowledge_found)
    score = sum((30, 25, 20, 15, 10)[index] for index, passed in enumerate(checks) if passed)
    root_cause = "SW-01 uplink failure" if all(checks[:4]) else "undetermined link failure"
    return root_cause, float(score)


def _render(result: DemoResult, output: TextIO) -> None:
    print("================================", file=output)
    print("NetworkOps AI Agent Demo", file=output)
    print("================================\n", file=output)
    print(f"Incident:\n{result.incident.incident_id}\n", file=output)
    print("Fault:\nSW-01 Gi0/1 interface down\n", file=output)
    print(f"Diagnosis:\nFinding:\n{result.diagnosis}\n", file=output)
    print(f"RCA:\nRoot Cause:\n{result.root_cause}\n", file=output)
    print(f"Repair Plan:\n{result.repair_plan}\n", file=output)
    print(f"Policy:\n{result.policy_decision}\n", file=output)
    print(f"Approval:\n{result.approval}\n", file=output)
    print(f"Execution:\n{result.execution}\n", file=output)
    print(f"Verification:\n{result.verification}\n", file=output)
    print("================================\n", file=output)
    print("Demo Completed", file=output)


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()


__all__ = ["DemoIncident", "DemoPaths", "DemoResult", "main", "run_demo"]
