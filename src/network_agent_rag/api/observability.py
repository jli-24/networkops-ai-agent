"""Read-only observability endpoints for enterprise incidents."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from network_agent_rag.api.enterprise_schemas import IncidentId
from network_agent_rag.observability import (
    IncidentTimelineBuilder,
    MetricsService,
    MetricsSnapshot,
    MetricsStore,
    SpanStatus,
)
from network_agent_rag.storage.base import TraceStore


observability_router = APIRouter(prefix="/observability")
enterprise_metrics_router = APIRouter(prefix="/enterprise")
metrics_router = APIRouter()


@observability_router.get("/incidents")
def list_observed_incidents(
    request: Request,
    status: SpanStatus | None = None,
    risk_level: Literal["low", "medium", "high", "critical"] | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        items, next_cursor = _trace_store(request).list_incidents(
            status=status,
            risk_level=risk_level,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": items, "next_cursor": next_cursor}


@observability_router.get("/incidents/{incident_id}/trace")
def incident_trace(
    incident_id: IncidentId,
    request: Request,
    status: SpanStatus | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    spans = _trace_store(request).list_spans(incident_id)
    if not spans:
        raise HTTPException(status_code=404, detail="Observed incident not found")
    trace_id = spans[0].trace_id
    if status is not None:
        spans = [span for span in spans if span.status == status]
    page, next_cursor = _page(spans, cursor, limit)
    return {
        "incident_id": incident_id,
        "trace_id": trace_id,
        "spans": page,
        "next_cursor": next_cursor,
    }


@observability_router.get("/incidents/{incident_id}/timeline")
def incident_timeline(
    incident_id: IncidentId,
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    store = _trace_store(request)
    if not store.list_spans(incident_id):
        raise HTTPException(status_code=404, detail="Observed incident not found")
    audit_log = getattr(request.app.state, "audit_log", None)
    events = IncidentTimelineBuilder(store, audit_log).build(incident_id)
    page, next_cursor = _page(events, cursor, limit)
    return {"incident_id": incident_id, "events": page, "next_cursor": next_cursor}


@observability_router.get("/metrics/summary")
def metrics_summary(request: Request) -> dict[str, object]:
    return MetricsService(_trace_store(request)).summary()


@metrics_router.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request) -> Response:
    content = MetricsService(_trace_store(request)).render_prometheus()
    return Response(content=content, media_type="text/plain; version=0.0.4")


@enterprise_metrics_router.get("/metrics", response_model=MetricsSnapshot)
def enterprise_metrics(request: Request) -> MetricsSnapshot:
    store: MetricsStore | None = getattr(request.app.state, "metrics_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Metrics store is not configured")
    return store.snapshot()


def _trace_store(request: Request) -> TraceStore:
    store = getattr(request.app.state, "trace_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trace store is not configured")
    return store


def _page(items: list[object], cursor: str | None, limit: int) -> tuple[list[object], str | None]:
    try:
        offset = int(cursor) if cursor is not None else 0
    except ValueError as error:
        raise HTTPException(status_code=422, detail="cursor must be a non-negative integer") from error
    if offset < 0:
        raise HTTPException(status_code=422, detail="cursor must be a non-negative integer")
    page = items[offset : offset + limit]
    next_offset = offset + len(page)
    return page, str(next_offset) if next_offset < len(items) else None


__all__ = [
    "enterprise_metrics_router",
    "metrics_router",
    "observability_router",
]
