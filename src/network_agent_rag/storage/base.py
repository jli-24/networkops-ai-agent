"""Structural contracts for pluggable NetworkOps persistence backends."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

from network_agent_rag.audit.models import AuditEvent, AuditEventType

if TYPE_CHECKING:
    from network_agent_rag.observability.models import (
        IncidentSummary,
        SpanKind,
        SpanStatus,
        TraceSpan,
    )
else:
    IncidentSummary = SpanKind = SpanStatus = TraceSpan = Any


@runtime_checkable
class AuditStore(Protocol):
    """Append-only enterprise audit storage contract."""

    def record(
        self,
        *,
        incident_id: str,
        event_type: AuditEventType | str,
        actor: str,
        action: str,
        outcome: str,
        details: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> AuditEvent: ...

    def list_events(
        self,
        incident_id: str,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]: ...

    def list_all_events(
        self,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]: ...


@runtime_checkable
class TraceStore(Protocol):
    """Execution-span storage contract used by observability projections."""

    def start_span(
        self,
        *,
        trace_id: str,
        run_id: str,
        incident_id: str,
        kind: SpanKind | str,
        name: str,
        parent_span_id: str | None = None,
        started_at: datetime | None = None,
        attributes: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        input_summary_hash: str | None = None,
    ) -> TraceSpan: ...

    def finish_span(
        self,
        span_id: str,
        *,
        status: SpanStatus | str,
        ended_at: datetime | None = None,
        error_code: str | None = None,
        attributes: dict[str, object] | None = None,
        output_summary_hash: str | None = None,
    ) -> TraceSpan: ...

    def list_spans(
        self,
        incident_id: str,
        *,
        run_id: str | None = None,
    ) -> list[TraceSpan]: ...

    def trace_id_for_incident(self, incident_id: str) -> str | None: ...

    def list_all_spans(self) -> list[TraceSpan]: ...

    def list_incidents(
        self,
        *,
        status: SpanStatus | str | None = None,
        risk_level: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[IncidentSummary], str | None]: ...


__all__ = ["AuditStore", "TraceStore"]
