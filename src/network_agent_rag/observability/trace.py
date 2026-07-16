"""Read validated TraceEvent records from the append-only Audit log."""

from __future__ import annotations

from network_agent_rag.audit import AuditEventType
from network_agent_rag.observability.models import TraceEvent
from network_agent_rag.storage.base import AuditStore


def load_trace_events(
    audit_log: AuditStore,
    incident_id: str,
) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for audit_event in audit_log.list_events(
        incident_id,
        event_type=AuditEventType.TRACE,
    ):
        payload = audit_event.details.get("trace_event")
        if not isinstance(payload, dict):
            raise ValueError("trace audit event must contain a trace_event object")
        events.append(TraceEvent.model_validate(payload))
    return events


__all__ = ["load_trace_events"]
