"""Authenticated, read-only projections for the Operator Console."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from network_agent_rag.api.enterprise_schemas import IncidentId
from network_agent_rag.audit import AuditEventType
from network_agent_rag.auth import (
    AuthenticationError,
    Permission,
    UserContext,
)
from network_agent_rag.auth.dependencies import (
    current_user_dependency,
    require_permission as require_api_permission,
)
from network_agent_rag.evaluation import BenchmarkReport
from network_agent_rag.governance import (
    GovernanceService,
    GovernanceSeverity,
    SecurityEventType,
)
from network_agent_rag.observability import SpanKind, SpanStatus
from network_agent_rag.observability.metrics import MetricsStore
from network_agent_rag.policy import PolicyEffect, PolicyOperation, PolicyRiskLevel
from network_agent_rag.storage.base import AuditStore, TraceStore


class EvaluationReportProvider(Protocol):
    """Read-only source for ephemeral v2 evaluation summaries."""

    def list_reports(self) -> Sequence[BenchmarkReport]: ...


class _ConsoleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DashboardResponse(_ConsoleModel):
    active_incidents: int = Field(ge=0)
    high_risk_operations: int = Field(ge=0)
    policy_denied_count: int = Field(ge=0)
    agent_success_rate: float = Field(ge=0, le=1)
    rca_accuracy: float | None = Field(default=None, ge=0, le=1)
    workflow_latency_ms: float | None = Field(default=None, ge=0)


class IncidentSummaryResponse(_ConsoleModel):
    incident_id: str
    status: str
    risk: str | None
    started_at: AwareDatetime
    updated_at: AwareDatetime
    run_count: int = Field(ge=1)
    span_count: int = Field(ge=1)


class IncidentListResponse(_ConsoleModel):
    items: tuple[IncidentSummaryResponse, ...]
    next_cursor: str | None


class TimelineItemResponse(_ConsoleModel):
    source: Literal["trace", "audit"]
    event_type: str
    name: str
    status: str
    timestamp: AwareDatetime
    latency_ms: float | None = Field(default=None, ge=0)
    error_code: str | None = None


class IncidentDetailResponse(_ConsoleModel):
    incident_id: str
    status: str
    risk: str | None
    current_stage: str | None
    approval_status: str | None
    execution_status: str | None
    timeline: tuple[TimelineItemResponse, ...]
    timeline_next_cursor: str | None


class TraceItemResponse(_ConsoleModel):
    span_id: str
    parent_id: str | None
    node: str | None
    agent: str | None
    tool: str | None
    status: str
    latency_ms: float | None = Field(default=None, ge=0)
    timestamp: AwareDatetime
    error_code: str | None


class TraceListResponse(_ConsoleModel):
    items: tuple[TraceItemResponse, ...]
    next_cursor: str | None


class SecurityEventResponse(_ConsoleModel):
    incident_id: str
    actor_id: str
    event_type: str
    severity: str
    decision: str
    timestamp: AwareDatetime


class SecurityEventListResponse(_ConsoleModel):
    items: tuple[SecurityEventResponse, ...]
    next_cursor: str | None


class PolicyDecisionResponse(_ConsoleModel):
    incident_id: str
    policy_id: str
    decision: str
    operation: str
    risk_level: str
    timestamp: AwareDatetime


class PolicyDecisionListResponse(_ConsoleModel):
    items: tuple[PolicyDecisionResponse, ...]
    next_cursor: str | None


class EvaluationSummaryResponse(_ConsoleModel):
    source: Literal["legacy", "evaluation"]
    dataset_version: str
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    rca_accuracy: float | None = Field(default=None, ge=0, le=1)
    top1_accuracy: float | None = Field(default=None, ge=0, le=1)
    top3_accuracy: float | None = Field(default=None, ge=0, le=1)
    created_at: AwareDatetime


class EvaluationListResponse(_ConsoleModel):
    items: tuple[EvaluationSummaryResponse, ...]
    next_cursor: str | None


console_router = APIRouter(prefix="/console")


def _console_permission(
    permission: Permission,
) -> Callable[[Request, UserContext | None], UserContext]:
    permission_check = require_api_permission(permission)

    def dependency(
        request: Request,
        user_context: Annotated[
            UserContext | None,
            Depends(current_user_dependency),
        ],
    ) -> UserContext:
        if getattr(request.app.state, "authentication_provider", None) is None:
            raise AuthenticationError()
        checked = permission_check(request, user_context)
        if checked is None:
            raise AuthenticationError()
        return checked

    return dependency


_require_incident_view = _console_permission(Permission.VIEW_INCIDENT)
_require_trace_view = _console_permission(Permission.VIEW_TRACE)


@console_router.get("/dashboard", response_model=DashboardResponse)
def console_dashboard(
    request: Request,
    _: Annotated[UserContext, Depends(_require_trace_view)],
) -> DashboardResponse:
    trace_store = _trace_store(request)
    audit_store = _audit_store(request)
    spans = trace_store.list_all_spans()
    agents = [
        span
        for span in spans
        if span.kind == SpanKind.AGENT and span.status != SpanStatus.RUNNING
    ]
    agent_success_rate = (
        sum(span.status == SpanStatus.SUCCEEDED for span in agents) / len(agents)
        if agents
        else 0.0
    )
    incidents = _all_incidents(trace_store)
    audits = audit_store.list_all_events(event_type=AuditEventType.DECISION)
    evaluations = _evaluation_items(request)
    metrics_store: MetricsStore | None = getattr(
        request.app.state, "metrics_store", None
    )
    latency = (
        metrics_store.snapshot().workflow_latency.average_ms
        if metrics_store is not None
        else None
    )
    return DashboardResponse(
        active_incidents=sum(
            incident.status in {SpanStatus.RUNNING, SpanStatus.INTERRUPTED}
            for incident in incidents
        ),
        high_risk_operations=sum(
            event.action == "risk_assessment_created"
            and event.details.get("risk_level") in {"high", "critical"}
            for event in audits
        ),
        policy_denied_count=sum(event.action == "policy_denied" for event in audits),
        agent_success_rate=round(agent_success_rate, 4),
        rca_accuracy=(evaluations[0].rca_accuracy if evaluations else None),
        workflow_latency_ms=latency,
    )


@console_router.get("/incidents", response_model=IncidentListResponse)
def console_incidents(
    request: Request,
    _: Annotated[UserContext, Depends(_require_incident_view)],
    status: SpanStatus | None = None,
    risk: Literal["low", "medium", "high", "critical"] | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> IncidentListResponse:
    try:
        incidents, next_cursor = _trace_store(request).list_incidents(
            status=status,
            risk_level=risk,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return IncidentListResponse(
        items=tuple(
            IncidentSummaryResponse(
                incident_id=_safe_text(item.incident_id),
                status=item.status.value,
                risk=item.risk_level,
                started_at=item.started_at,
                updated_at=item.updated_at,
                run_count=item.run_count,
                span_count=item.span_count,
            )
            for item in incidents
        ),
        next_cursor=next_cursor,
    )


@console_router.get(
    "/incidents/{incident_id}", response_model=IncidentDetailResponse
)
async def console_incident_detail(
    incident_id: IncidentId,
    request: Request,
    _: Annotated[UserContext, Depends(_require_incident_view)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> IncidentDetailResponse:
    workflow = getattr(request.app.state, "enterprise_workflow", None)
    if workflow is None:
        raise HTTPException(
            status_code=503, detail="Enterprise workflow is not configured"
        )
    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": incident_id}}
    )
    values = dict(snapshot.values)
    if not values:
        raise HTTPException(status_code=404, detail="Incident not found")
    risk = values.get("risk_decision")
    approval = values.get("approval_result")
    execution = values.get("execution_result")
    timeline, next_cursor = _page(
        _incident_timeline(request, incident_id), cursor, limit
    )
    return IncidentDetailResponse(
        incident_id=_safe_text(incident_id),
        status=_safe_text(values.get("enterprise_status")) or "unknown",
        risk=_mapping_text(risk, "risk_level"),
        current_stage=_safe_optional(values.get("current_agent")),
        approval_status=_mapping_text(approval, "decision"),
        execution_status=_mapping_text(execution, "status"),
        timeline=tuple(timeline),
        timeline_next_cursor=next_cursor,
    )


@console_router.get(
    "/incidents/{incident_id}/trace", response_model=TraceListResponse
)
def console_incident_trace(
    incident_id: IncidentId,
    request: Request,
    _: Annotated[UserContext, Depends(_require_trace_view)],
    status: SpanStatus | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> TraceListResponse:
    spans = _trace_store(request).list_spans(incident_id)
    if not spans:
        raise HTTPException(status_code=404, detail="Observed incident not found")
    if status is not None:
        spans = [span for span in spans if span.status == status]
    page, next_cursor = _page(spans, cursor, limit)
    return TraceListResponse(
        items=tuple(_trace_item(span) for span in page),
        next_cursor=next_cursor,
    )


@console_router.get("/security-events", response_model=SecurityEventListResponse)
def console_security_events(
    request: Request,
    _: Annotated[UserContext, Depends(_require_trace_view)],
    incident: str | None = None,
    actor: str | None = None,
    event_type: SecurityEventType | None = None,
    severity: GovernanceSeverity | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> SecurityEventListResponse:
    events = GovernanceService(
        _audit_store(request), _trace_store(request)
    ).query_events(
        incident_id=incident,
        actor_id=actor,
        event_type=event_type,
        severity=severity,
    )
    page, next_cursor = _page(events, cursor, limit)
    return SecurityEventListResponse(
        items=tuple(
            SecurityEventResponse(
                incident_id=_safe_text(event.incident_id),
                actor_id=_safe_text(event.actor_id),
                event_type=event.event_type.value,
                severity=event.severity.value,
                decision=_safe_text(event.decision),
                timestamp=event.timestamp,
            )
            for event in page
        ),
        next_cursor=next_cursor,
    )


@console_router.get("/policy-decisions", response_model=PolicyDecisionListResponse)
def console_policy_decisions(
    request: Request,
    _: Annotated[UserContext, Depends(_require_trace_view)],
    incident: str | None = None,
    decision: PolicyEffect | None = None,
    operation: PolicyOperation | None = None,
    risk: PolicyRiskLevel | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> PolicyDecisionListResponse:
    events = (
        _audit_store(request).list_events(incident, event_type=AuditEventType.DECISION)
        if incident is not None
        else _audit_store(request).list_all_events(event_type=AuditEventType.DECISION)
    )
    policy_actions = {
        "policy_evaluation",
        "policy_denied",
        "policy_approval_required",
    }
    projected = [
        _policy_item(event)
        for event in events
        if event.action in policy_actions
        and (decision is None or event.details.get("decision") == decision.value)
        and (operation is None or event.details.get("operation") == operation.value)
        and (risk is None or event.details.get("risk_level") == risk.value)
    ]
    projected = [item for item in projected if item is not None]
    page, next_cursor = _page(projected, cursor, limit)
    return PolicyDecisionListResponse(items=tuple(page), next_cursor=next_cursor)


@console_router.get("/evaluations", response_model=EvaluationListResponse)
def console_evaluations(
    request: Request,
    _: Annotated[UserContext, Depends(_require_trace_view)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> EvaluationListResponse:
    items = _evaluation_items(request)
    page, next_cursor = _page(items, cursor, limit)
    return EvaluationListResponse(items=tuple(page), next_cursor=next_cursor)


def _trace_store(request: Request) -> TraceStore:
    store = getattr(request.app.state, "trace_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trace store is not configured")
    return store


def _audit_store(request: Request) -> AuditStore:
    store = getattr(request.app.state, "audit_log", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Audit log is not configured")
    return store


def _all_incidents(store: TraceStore) -> list[object]:
    incidents: list[object] = []
    cursor: str | None = None
    while True:
        page, cursor = store.list_incidents(cursor=cursor, limit=100)
        incidents.extend(page)
        if cursor is None:
            return incidents


def _incident_timeline(
    request: Request, incident_id: str
) -> tuple[TimelineItemResponse, ...]:
    items: list[TimelineItemResponse] = []
    trace_store = getattr(request.app.state, "trace_store", None)
    if trace_store is not None:
        for span in trace_store.list_spans(incident_id):
            items.append(
                TimelineItemResponse(
                    source="trace",
                    event_type=span.kind.value,
                    name=_safe_text(span.name),
                    status=span.status.value,
                    timestamp=span.ended_at or span.started_at,
                    latency_ms=span.duration_ms,
                    error_code=_safe_optional(span.error_code),
                )
            )
    audit_store = getattr(request.app.state, "audit_log", None)
    if audit_store is not None:
        for event in audit_store.list_events(incident_id):
            items.append(
                TimelineItemResponse(
                    source="audit",
                    event_type=event.event_type.value,
                    name=_safe_text(event.action),
                    status=_safe_text(event.outcome),
                    timestamp=event.created_at,
                )
            )
    return tuple(sorted(items, key=lambda item: (item.timestamp, item.source, item.name)))


def _trace_item(span: object) -> TraceItemResponse:
    name = _safe_text(span.name)
    return TraceItemResponse(
        span_id=_safe_text(span.span_id),
        parent_id=_safe_optional(span.parent_span_id),
        node=name if span.kind not in {SpanKind.AGENT, SpanKind.TOOL} else None,
        agent=name if span.kind == SpanKind.AGENT else None,
        tool=name if span.kind == SpanKind.TOOL else None,
        status=span.status.value,
        latency_ms=span.duration_ms,
        timestamp=span.ended_at or span.started_at,
        error_code=_safe_optional(span.error_code),
    )


def _policy_item(event: object) -> PolicyDecisionResponse | None:
    values = event.details
    required = ("policy_id", "decision", "operation", "risk_level")
    if not all(isinstance(values.get(field), str) for field in required):
        return None
    return PolicyDecisionResponse(
        incident_id=_safe_text(event.incident_id),
        policy_id=f"policy-event-{_safe_text(event.event_id)}",
        decision=_safe_text(values["decision"]),
        operation=_safe_text(values["operation"]),
        risk_level=_safe_text(values["risk_level"]),
        timestamp=event.created_at,
    )


def _evaluation_items(request: Request) -> list[EvaluationSummaryResponse]:
    items: list[EvaluationSummaryResponse] = []
    legacy_store = getattr(request.app.state, "benchmark_store", None)
    if legacy_store is not None:
        for run in legacy_store.list_runs():
            items.append(
                EvaluationSummaryResponse(
                    source="legacy",
                    dataset_version=_safe_text(run.dataset_version),
                    total_cases=int(run.summary.get("cases_total", len(run.cases))),
                    passed_cases=int(
                        run.summary.get(
                            "cases_passed", sum(case.passed for case in run.cases)
                        )
                    ),
                    rca_accuracy=_optional_ratio(
                        run.summary.get("root_cause_accuracy")
                    ),
                    top1_accuracy=None,
                    top3_accuracy=None,
                    created_at=run.finished_at,
                )
            )
    provider: EvaluationReportProvider | None = getattr(
        request.app.state, "evaluation_report_provider", None
    )
    if provider is not None:
        for report in provider.list_reports():
            items.append(
                EvaluationSummaryResponse(
                    source="evaluation",
                    dataset_version=_safe_text(report.dataset_version),
                    total_cases=report.total_cases,
                    passed_cases=report.passed_cases,
                    rca_accuracy=report.metrics.rca_accuracy,
                    top1_accuracy=report.metrics.top1_accuracy,
                    top3_accuracy=report.metrics.top3_accuracy,
                    created_at=report.created_at,
                )
            )
    return sorted(
        items,
        key=lambda item: (item.created_at, item.dataset_version, item.source),
        reverse=True,
    )


def _page(
    items: Sequence[object], cursor: str | None, limit: int
) -> tuple[list[object], str | None]:
    try:
        offset = int(cursor) if cursor is not None else 0
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="cursor must be a non-negative integer"
        ) from error
    if offset < 0:
        raise HTTPException(
            status_code=422, detail="cursor must be a non-negative integer"
        )
    page = list(items[offset : offset + limit])
    next_offset = offset + len(page)
    return page, str(next_offset) if next_offset < len(items) else None


def _mapping_text(value: object, key: str) -> str | None:
    if not isinstance(value, dict):
        return None
    return _safe_optional(value.get(key))


def _optional_ratio(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 1 else None


def _safe_optional(value: object) -> str | None:
    return _safe_text(value) if isinstance(value, str) and value.strip() else None


def _safe_text(value: object) -> str:
    text = str(value).strip()
    lowered = text.casefold()
    forbidden = (
        "token",
        "secret",
        "password",
        "prompt",
        "credential",
        "hash",
        "authorization",
        "api_key",
    )
    return "redacted" if any(item in lowered for item in forbidden) else text


__all__ = [
    "EvaluationReportProvider",
    "console_router",
]
