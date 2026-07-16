"""Low-cardinality metrics derived from persisted trace spans."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from datetime import timezone
from math import ceil, floor
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from network_agent_rag.audit import AuditEventType
from network_agent_rag.auth import Permission
from network_agent_rag.observability.models import (
    SpanKind,
    SpanStatus,
    TraceEvent,
    TraceEventType,
    TraceSpan,
)
from network_agent_rag.storage.base import AuditStore, TraceStore


class _MetricsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LatencySummary(_MetricsModel):
    count: int = Field(ge=0)
    average_ms: float = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_summary(self) -> "LatencySummary":
        values = (self.average_ms, self.p50_ms, self.p95_ms, self.max_ms)
        if self.count == 0 and any(values):
            raise ValueError("empty latency summary must contain only zero values")
        if not self.p50_ms <= self.p95_ms <= self.max_ms:
            raise ValueError("latency percentiles must be ordered")
        if self.average_ms > self.max_ms:
            raise ValueError("average latency cannot exceed maximum latency")
        return self


class CallSummary(_MetricsModel):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    interrupted: int = Field(ge=0)
    failure_rate_percent: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_summary(self) -> "CallSummary":
        if self.total != self.succeeded + self.failed + self.interrupted:
            raise ValueError("tool call total must equal status counts")
        expected = round(self.failed / self.total * 100, 2) if self.total else 0.0
        if self.failure_rate_percent != expected:
            raise ValueError("tool failure rate does not match counts")
        return self


class RagRetrievalSummary(_MetricsModel):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    interrupted: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_summary(self) -> "RagRetrievalSummary":
        if self.total != self.succeeded + self.failed + self.interrupted:
            raise ValueError("RAG total must equal status counts")
        return self


class RepairSummary(_MetricsModel):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    not_executed: int = Field(ge=0)
    success_rate_percent: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_summary(self) -> "RepairSummary":
        statuses = self.succeeded + self.failed + self.blocked + self.not_executed
        if self.total != statuses:
            raise ValueError("repair total must equal status counts")
        expected = round(self.succeeded / self.total * 100, 2) if self.total else 0.0
        if self.success_rate_percent != expected:
            raise ValueError("repair success rate does not match counts")
        return self


class MetricsSnapshot(_MetricsModel):
    workflow_latency: LatencySummary
    agent_latency: LatencySummary
    tool_calls: CallSummary
    rag_retrievals: RagRetrievalSummary
    approval_waiting_time: LatencySummary
    repair_executions: RepairSummary


class PermissionAuthorizationSummary(_MetricsModel):
    permission: Permission
    total: int = Field(ge=0)
    allowed: int = Field(ge=0)
    denied: int = Field(ge=0)
    failure_rate_percent: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_summary(self) -> "PermissionAuthorizationSummary":
        if self.total != self.allowed + self.denied:
            raise ValueError("authorization total must equal decision counts")
        expected = round(self.denied / self.total * 100, 2) if self.total else 0.0
        if self.failure_rate_percent != expected:
            raise ValueError("authorization failure rate does not match counts")
        return self


class AuthorizationMetrics(_MetricsModel):
    authorization_total: int = Field(ge=0)
    authorization_allowed: int = Field(ge=0)
    authorization_denied: int = Field(ge=0)
    authorization_failure_rate: float = Field(ge=0, le=100)
    by_permission: tuple[PermissionAuthorizationSummary, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> "AuthorizationMetrics":
        if self.authorization_total != (
            self.authorization_allowed + self.authorization_denied
        ):
            raise ValueError("authorization total must equal decision counts")
        expected = (
            round(self.authorization_denied / self.authorization_total * 100, 2)
            if self.authorization_total
            else 0.0
        )
        if self.authorization_failure_rate != expected:
            raise ValueError("authorization failure rate does not match counts")
        return self


def summarize_authorization(
    decisions: Iterable[tuple[Permission, str]],
) -> AuthorizationMetrics:
    counts = Counter(decisions)
    by_permission: list[PermissionAuthorizationSummary] = []
    for permission in Permission:
        allowed = counts[(permission, "allowed")]
        denied = counts[(permission, "denied")]
        total = allowed + denied
        by_permission.append(
            PermissionAuthorizationSummary(
                permission=permission,
                total=total,
                allowed=allowed,
                denied=denied,
                failure_rate_percent=(
                    round(denied / total * 100, 2) if total else 0.0
                ),
            )
        )
    allowed_total = sum(item.allowed for item in by_permission)
    denied_total = sum(item.denied for item in by_permission)
    total = allowed_total + denied_total
    return AuthorizationMetrics(
        authorization_total=total,
        authorization_allowed=allowed_total,
        authorization_denied=denied_total,
        authorization_failure_rate=(
            round(denied_total / total * 100, 2) if total else 0.0
        ),
        by_permission=tuple(by_permission),
    )


class MetricsStore(Protocol):
    def snapshot(self) -> MetricsSnapshot: ...


class SQLiteMetricsStore:
    def __init__(
        self,
        trace_store: TraceStore,
        audit_log: AuditStore | None = None,
    ) -> None:
        self.trace_store = trace_store
        self.audit_log = audit_log

    def snapshot(self) -> MetricsSnapshot:
        spans = self.trace_store.list_all_spans()
        completed = [
            span
            for span in spans
            if span.status != SpanStatus.RUNNING and span.duration_ms is not None
        ]
        tools = [span for span in completed if span.kind == SpanKind.TOOL]
        rag = [span for span in tools if span.name == "search_knowledge"]
        executions = [span for span in completed if span.kind == SpanKind.EXECUTION]
        return MetricsSnapshot(
            workflow_latency=_latency_summary(
                [
                    span.duration_ms
                    for span in completed
                    if span.kind == SpanKind.WORKFLOW
                ]
            ),
            agent_latency=_latency_summary(
                [span.duration_ms for span in completed if span.kind == SpanKind.AGENT]
            ),
            tool_calls=_call_summary(tools),
            rag_retrievals=_rag_summary(rag),
            approval_waiting_time=_latency_summary(
                _approval_waiting_times(self.audit_log)
            ),
            repair_executions=_repair_summary(executions),
        )


def _latency_summary(values: list[float]) -> LatencySummary:
    samples = sorted(float(value) for value in values)
    if not samples:
        return LatencySummary(
            count=0,
            average_ms=0.0,
            p50_ms=0.0,
            p95_ms=0.0,
            max_ms=0.0,
        )
    return LatencySummary(
        count=len(samples),
        average_ms=round(sum(samples) / len(samples), 3),
        p50_ms=_percentile(samples, 0.5),
        p95_ms=_percentile(samples, 0.95),
        max_ms=round(samples[-1], 3),
    )


def _percentile(values: list[float], percentile: float) -> float:
    rank = (len(values) - 1) * percentile
    lower = floor(rank)
    upper = ceil(rank)
    if lower == upper:
        return round(values[lower], 3)
    result = values[lower] + (values[upper] - values[lower]) * (rank - lower)
    return round(result, 3)


def _call_summary(spans: list[TraceSpan]) -> CallSummary:
    counts = Counter(span.status for span in spans)
    total = len(spans)
    failed = counts[SpanStatus.FAILED]
    return CallSummary(
        total=total,
        succeeded=counts[SpanStatus.SUCCEEDED],
        failed=failed,
        interrupted=counts[SpanStatus.INTERRUPTED],
        failure_rate_percent=round(failed / total * 100, 2) if total else 0.0,
    )


def _rag_summary(spans: list[TraceSpan]) -> RagRetrievalSummary:
    counts = Counter(span.status for span in spans)
    return RagRetrievalSummary(
        total=len(spans),
        succeeded=counts[SpanStatus.SUCCEEDED],
        failed=counts[SpanStatus.FAILED],
        interrupted=counts[SpanStatus.INTERRUPTED],
    )


def _repair_summary(spans: list[TraceSpan]) -> RepairSummary:
    counts: Counter[str] = Counter()
    for span in spans:
        status = span.attributes.get("execution_status")
        if status in {"succeeded", "failed", "blocked", "not_executed"}:
            counts[str(status)] += 1
    total = sum(counts.values())
    return RepairSummary(
        total=total,
        succeeded=counts["succeeded"],
        failed=counts["failed"],
        blocked=counts["blocked"],
        not_executed=counts["not_executed"],
        success_rate_percent=(
            round(counts["succeeded"] / total * 100, 2) if total else 0.0
        ),
    )


def _approval_waiting_times(audit_log: AuditStore | None) -> list[float]:
    if audit_log is None:
        return []
    pending: dict[tuple[str, str], deque[TraceEvent]] = defaultdict(deque)
    durations: list[float] = []
    for audit_event in audit_log.list_all_events(event_type=AuditEventType.TRACE):
        payload = audit_event.details.get("trace_event")
        if not isinstance(payload, dict):
            continue
        try:
            event = TraceEvent.model_validate(payload)
        except ValidationError:
            continue
        key = (event.incident_id, event.trace_id)
        if event.event_type == TraceEventType.APPROVAL_PAUSE:
            pending[key].append(event)
        elif event.event_type == TraceEventType.RESUME and pending[key]:
            pause = pending[key].popleft()
            duration = (event.timestamp - pause.timestamp).total_seconds() * 1000
            if duration >= 0:
                durations.append(duration)
    return durations


class MetricsService:
    def __init__(self, trace_store: TraceStore) -> None:
        self.trace_store = trace_store

    def summary(self) -> dict[str, object]:
        spans = self.trace_store.list_all_spans()
        incidents = _latest_workflows(spans)
        status_counts = Counter(item.status.value for item in spans)
        incident_counts = Counter(item.status.value for item in incidents)
        rag_quality = _rag_quality(spans)
        return {
            "incidents_total": len(incidents),
            "active_incidents": incident_counts[SpanStatus.RUNNING.value],
            "pending_approvals": incident_counts[SpanStatus.INTERRUPTED.value],
            "spans_total": len(spans),
            "incidents_by_status": dict(sorted(incident_counts.items())),
            "spans_by_status": dict(sorted(status_counts.items())),
            "agent_calls_by_status": _kind_status_counts(spans, SpanKind.AGENT),
            "tool_calls_by_status": _kind_status_counts(spans, SpanKind.TOOL),
            "risk_levels": _incident_attribute_counts(spans, "risk_level"),
            "approval_decisions": _incident_attribute_counts(
                spans, "approval_decision"
            ),
            "execution_results": _incident_attribute_counts(
                spans, "execution_status"
            ),
            "rag_quality": rag_quality,
            "duration_ms_by_component": _duration_groups(spans),
            "incident_trend": _incident_trend(incidents),
        }

    def render_prometheus(self) -> str:
        spans = self.trace_store.list_all_spans()
        incidents = _latest_workflows(spans)
        lines = [
            "# HELP networkops_incidents_total Completed or active incident runs by status.",
            "# TYPE networkops_incidents_total gauge",
        ]
        for status, count in sorted(Counter(item.status.value for item in incidents).items()):
            lines.append(f'networkops_incidents_total{{status="{status}"}} {count}')
        lines.extend(
            [
                "# HELP networkops_pending_approvals Current interrupted incidents awaiting approval.",
                "# TYPE networkops_pending_approvals gauge",
                f"networkops_pending_approvals {sum(item.status == SpanStatus.INTERRUPTED for item in incidents)}",
                "# HELP networkops_spans_total Trace spans by component and status.",
                "# TYPE networkops_spans_total counter",
            ]
        )
        counts = Counter((span.kind.value, span.name, span.status.value) for span in spans)
        for (kind, name, status), count in sorted(counts.items()):
            lines.append(
                f'networkops_spans_total{{kind="{_escape(kind)}",name="{_escape(name)}",status="{_escape(status)}"}} {count}'
            )
        for metric, label, values in (
            ("networkops_incident_risk_total", "risk_level", _incident_attribute_counts(spans, "risk_level")),
            ("networkops_approval_decisions_total", "decision", _incident_attribute_counts(spans, "approval_decision")),
            ("networkops_execution_results_total", "status", _incident_attribute_counts(spans, "execution_status")),
        ):
            lines.extend(
                f'{metric}{{{label}="{_escape(value)}"}} {count}'
                for value, count in values.items()
            )
        rag = _rag_quality(spans)
        lines.extend(
            [
                f"networkops_rag_relevance_score_average {rag['relevance_score_average']}",
                f"networkops_rag_quality_iterations_average {rag['quality_iterations_average']}",
                f"networkops_rag_evidence_count_average {rag['evidence_count_average']}",
            ]
        )
        lines.extend(
            [
                "# HELP networkops_span_duration_ms Trace span duration in milliseconds.",
                "# TYPE networkops_span_duration_ms summary",
            ]
        )
        durations: dict[tuple[str, str], list[float]] = defaultdict(list)
        for span in spans:
            if span.duration_ms is not None:
                durations[(span.kind.value, span.name)].append(span.duration_ms)
        for (kind, name), values in sorted(durations.items()):
            labels = f'kind="{_escape(kind)}",name="{_escape(name)}"'
            lines.append(f"networkops_span_duration_ms_sum{{{labels}}} {sum(values)}")
            lines.append(f"networkops_span_duration_ms_count{{{labels}}} {len(values)}")
        return "\n".join(lines) + "\n"


def _duration_groups(spans: list[TraceSpan]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for span in spans:
        if span.duration_ms is not None:
            grouped[(span.kind.value, span.name)].append(span.duration_ms)
    return [
        {
            "kind": kind,
            "name": name,
            "count": len(values),
            "sum_ms": round(sum(values), 3),
            "average_ms": round(sum(values) / len(values), 3),
        }
        for (kind, name), values in sorted(grouped.items())
    ]


def _latest_workflows(spans: list[TraceSpan]) -> list[TraceSpan]:
    latest: dict[str, TraceSpan] = {}
    for span in spans:
        if span.kind == SpanKind.WORKFLOW:
            latest[span.incident_id] = span
    return list(latest.values())


def _incident_trend(workflows: list[TraceSpan]) -> list[dict[str, object]]:
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for span in workflows:
        bucket = span.started_at.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        ).isoformat()
        buckets[bucket][span.status.value] += 1
    return [
        {
            "bucket": bucket,
            "total": sum(counts.values()),
            "succeeded": counts[SpanStatus.SUCCEEDED.value],
            "failed": counts[SpanStatus.FAILED.value],
            "interrupted": counts[SpanStatus.INTERRUPTED.value],
            "running": counts[SpanStatus.RUNNING.value],
        }
        for bucket, counts in sorted(buckets.items())
    ]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _kind_status_counts(
    spans: list[TraceSpan], kind: SpanKind
) -> dict[str, int]:
    return dict(
        sorted(Counter(span.status.value for span in spans if span.kind == kind).items())
    )


def _incident_attribute_counts(
    spans: list[TraceSpan], attribute: str
) -> dict[str, int]:
    latest: dict[str, str] = {}
    for span in spans:
        value = span.attributes.get(attribute)
        if isinstance(value, str) and value:
            latest[span.incident_id] = value
    return dict(sorted(Counter(latest.values()).items()))


def _rag_quality(spans: list[TraceSpan]) -> dict[str, float]:
    fields = {
        "relevance_score_average": "relevance_score",
        "quality_iterations_average": "diagnosis_iteration",
        "evidence_count_average": "evidence_count",
    }
    result: dict[str, float] = {}
    for output_name, attribute in fields.items():
        latest: dict[str, float] = {}
        for span in spans:
            value = span.attributes.get(attribute)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                latest[span.incident_id] = float(value)
        result[output_name] = round(sum(latest.values()) / len(latest), 4) if latest else 0.0
    return result


__all__ = [
    "AuthorizationMetrics",
    "CallSummary",
    "LatencySummary",
    "MetricsService",
    "MetricsSnapshot",
    "MetricsStore",
    "PermissionAuthorizationSummary",
    "RagRetrievalSummary",
    "RepairSummary",
    "SQLiteMetricsStore",
    "summarize_authorization",
]
