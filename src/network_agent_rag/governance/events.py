"""Read-only security-event projections over Audit and Trace facts."""

from __future__ import annotations

from hashlib import sha256

from network_agent_rag.audit import AuditEvent, AuditEventType
from network_agent_rag.governance.models import (
    GovernanceSeverity,
    SecurityEvent,
    SecurityEventSource,
    SecurityEventType,
)
from network_agent_rag.observability import SpanKind, SpanStatus, TraceSpan
from network_agent_rag.observability.governance import (
    GovernanceDecision,
    project_governance_event,
)
from network_agent_rag.storage.base import AuditStore, TraceStore


_AUTHENTICATION_FAILURE_ACTIONS = {
    "authenticate_failed",
    "api_key_authenticate_failed",
    "refresh_token_failed",
}


class SecurityEventCenter:
    def __init__(self, audit_store: AuditStore, trace_store: TraceStore) -> None:
        self.audit_store = audit_store
        self.trace_store = trace_store

    def query(
        self,
        *,
        incident_id: str | None = None,
        actor_id: str | None = None,
        event_type: SecurityEventType | None = None,
        severity: GovernanceSeverity | None = None,
    ) -> tuple[SecurityEvent, ...]:
        events = self._project(incident_id)
        return tuple(
            event
            for event in events
            if (actor_id is None or event.actor_id == actor_id)
            and (event_type is None or event.event_type == event_type)
            and (severity is None or event.severity == severity)
        )

    def sync(self, incident_id: str | None = None) -> tuple[SecurityEvent, ...]:
        events = self._project(incident_id)
        for event in events:
            self.audit_store.record(
                incident_id=event.incident_id,
                event_type=AuditEventType.DECISION,
                actor="GovernanceService",
                action="security_event_created",
                outcome="created",
                details={
                    "security_event_id": event.event_id,
                    "source": event.source.value,
                    "source_event_id": event.source_event_id,
                    "event_type": event.event_type.value,
                    "actor_id": event.actor_id,
                    "action": event.action,
                    "decision": event.decision,
                    "severity": event.severity.value,
                    "source_timestamp": event.timestamp.isoformat(),
                },
                idempotency_key=(
                    f"security-event:{event.source.value}:"
                    f"{event.source_event_id}:{event.event_type.value}"
                ),
            )
        return events

    def _project(self, incident_id: str | None) -> tuple[SecurityEvent, ...]:
        audit_events = (
            self.audit_store.list_events(incident_id)
            if incident_id is not None
            else self.audit_store.list_all_events()
        )
        projected: list[SecurityEvent] = []
        audit_repairs: set[str] = set()
        for event in audit_events:
            mapped = _from_audit(event)
            projected.extend(mapped)
            if any(item.event_type == SecurityEventType.REPAIR_FAILED for item in mapped):
                audit_repairs.add(event.incident_id)

        spans = (
            self.trace_store.list_spans(incident_id)
            if incident_id is not None
            else self.trace_store.list_all_spans()
        )
        projected.extend(
            _from_trace(span)
            for span in spans
            if span.incident_id not in audit_repairs
            and span.kind == SpanKind.EXECUTION
            and span.status == SpanStatus.FAILED
        )
        unique = {event.event_id: event for event in projected}
        return tuple(sorted(unique.values(), key=lambda item: (item.timestamp, item.event_id)))


def _from_audit(event: AuditEvent) -> list[SecurityEvent]:
    mappings: list[tuple[SecurityEventType, GovernanceSeverity]] = []
    if event.action in _AUTHENTICATION_FAILURE_ACTIONS and event.outcome == "denied":
        mappings.append(
            (SecurityEventType.AUTHENTICATION_FAILED, GovernanceSeverity.MEDIUM)
        )
    authorization = project_governance_event(event)
    if (
        authorization is not None
        and authorization.decision == GovernanceDecision.DENIED
    ):
        mappings.append(
            (SecurityEventType.AUTHORIZATION_DENIED, GovernanceSeverity.MEDIUM)
        )
    if event.action == "policy_violation":
        mappings.append((SecurityEventType.POLICY_VIOLATION, GovernanceSeverity.HIGH))
    if event.action == "policy_denied" and event.outcome == "denied":
        mappings.append((SecurityEventType.POLICY_VIOLATION, GovernanceSeverity.HIGH))
    if (
        event.action == "policy_approval_required"
        and event.outcome == "approval_required"
    ):
        mappings.append((SecurityEventType.APPROVAL_REQUIRED, GovernanceSeverity.MEDIUM))
    if event.action == "evaluate_risk" and event.outcome == "approval_required":
        mappings.append((SecurityEventType.APPROVAL_REQUIRED, GovernanceSeverity.MEDIUM))
        level = event.details.get("risk_level")
        if level in {"high", "critical"}:
            mappings.append(
                (
                    SecurityEventType.HIGH_RISK_OPERATION,
                    GovernanceSeverity(level),
                )
            )
    if event.action == "risk_assessment_created":
        level = event.details.get("risk_level")
        if level in {"high", "critical"}:
            mappings.append(
                (
                    SecurityEventType.HIGH_RISK_OPERATION,
                    GovernanceSeverity(level),
                )
            )
    if (
        event.event_type == AuditEventType.TOOL_CALL
        and event.actor == "Execute"
        and event.outcome == "failed"
    ):
        mappings.append((SecurityEventType.REPAIR_FAILED, GovernanceSeverity.HIGH))
    if event.action == "session_revoked":
        mappings.append((SecurityEventType.SESSION_REVOKED, GovernanceSeverity.MEDIUM))

    actor = event.actor_id or event.actor
    return [
        SecurityEvent(
            event_id=_event_id("audit", event.event_id, kind.value),
            incident_id=event.incident_id,
            source_event_id=event.event_id,
            event_type=kind,
            actor_id=actor,
            source=SecurityEventSource.AUDIT,
            action=event.action,
            decision=event.outcome,
            severity=severity,
            timestamp=event.created_at,
        )
        for kind, severity in mappings
    ]


def _from_trace(span: TraceSpan) -> SecurityEvent:
    return SecurityEvent(
        event_id=_event_id("trace", span.span_id, SecurityEventType.REPAIR_FAILED.value),
        incident_id=span.incident_id,
        source_event_id=span.span_id,
        event_type=SecurityEventType.REPAIR_FAILED,
        actor_id="system",
        source=SecurityEventSource.TRACE,
        action=span.name,
        decision=span.status.value,
        severity=GovernanceSeverity.HIGH,
        timestamp=span.ended_at or span.started_at,
    )


def _event_id(source: str, source_id: str, event_type: str) -> str:
    digest = sha256(f"{source}:{source_id}:{event_type}".encode()).hexdigest()[:24]
    return f"security-{digest}"


__all__ = ["SecurityEventCenter"]
