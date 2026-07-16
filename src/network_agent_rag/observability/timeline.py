"""Incident timeline projection over trace and audit records."""

from __future__ import annotations

from network_agent_rag.observability.models import TimelineEvent
from network_agent_rag.storage.base import AuditStore, TraceStore


class IncidentTimelineBuilder:
    def __init__(
        self,
        trace_store: TraceStore,
        audit_log: AuditStore | None = None,
    ) -> None:
        self.trace_store = trace_store
        self.audit_log = audit_log

    def build(self, incident_id: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        for span in self.trace_store.list_spans(incident_id):
            events.append(
                TimelineEvent(
                    event_id=f"{span.span_id}:start",
                    incident_id=incident_id,
                    source="trace",
                    event_type="span_started",
                    name=span.name,
                    status="running",
                    timestamp=span.started_at,
                    details={"kind": span.kind.value, "run_id": span.run_id},
                )
            )
            if span.ended_at is not None:
                events.append(
                    TimelineEvent(
                        event_id=f"{span.span_id}:end",
                        incident_id=incident_id,
                        source="trace",
                        event_type="span_completed",
                        name=span.name,
                        status=span.status.value,
                        timestamp=span.ended_at,
                        duration_ms=span.duration_ms,
                        details={"kind": span.kind.value, "error_code": span.error_code},
                    )
                )
        if self.audit_log is not None:
            for event in self.audit_log.list_events(incident_id):
                events.append(
                    TimelineEvent(
                        event_id=event.event_id,
                        incident_id=incident_id,
                        source="audit",
                        event_type=event.event_type.value,
                        name=event.action,
                        status=event.outcome,
                        timestamp=event.created_at,
                        details={"actor": event.actor, **event.details},
                    )
                )
        return sorted(events, key=lambda item: (item.timestamp, item.event_id))


__all__ = ["IncidentTimelineBuilder"]
