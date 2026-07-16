"""Read-only governance projections over authorization audit decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
)

from network_agent_rag.audit import AuditEvent, AuditEventType
from network_agent_rag.auth import Permission
from network_agent_rag.observability.metrics import (
    AuthorizationMetrics,
    summarize_authorization,
)
from network_agent_rag.storage.base import AuditStore, TraceStore


_Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class GovernanceSource(StrEnum):
    API = "api"
    WORKFLOW = "workflow"
    SYSTEM = "system"


class GovernanceDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


class GovernanceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: _Identifier
    timestamp: AwareDatetime
    incident_id: _Identifier
    actor_id: _Identifier
    actor_role: str | None = None
    permission: Permission
    decision: GovernanceDecision
    source: GovernanceSource
    trace_id: _Identifier | None = None

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


_AUTHORIZATION_ACTIONS: dict[str, tuple[GovernanceSource, Permission]] = {
    "authorize_api_view_incident": (
        GovernanceSource.API,
        Permission.VIEW_INCIDENT,
    ),
    "authorize_api_view_trace": (
        GovernanceSource.API,
        Permission.VIEW_TRACE,
    ),
    "authorize_api_repair_plan": (
        GovernanceSource.API,
        Permission.CREATE_REPAIR_PLAN,
    ),
    "authorize_api_approval": (
        GovernanceSource.API,
        Permission.APPROVE_REPAIR,
    ),
    "authorize_api_execution": (
        GovernanceSource.API,
        Permission.EXECUTE_REPAIR,
    ),
    "authorize_api_system": (
        GovernanceSource.API,
        Permission.MANAGE_SYSTEM,
    ),
    "authorize_repair_plan": (
        GovernanceSource.WORKFLOW,
        Permission.CREATE_REPAIR_PLAN,
    ),
    "authorize_approval": (
        GovernanceSource.WORKFLOW,
        Permission.APPROVE_REPAIR,
    ),
    "authorize_execution": (
        GovernanceSource.WORKFLOW,
        Permission.EXECUTE_REPAIR,
    ),
}


def project_governance_event(
    event: AuditEvent,
    trace_store: TraceStore | None = None,
) -> GovernanceEvent | None:
    """Project one recognized authorization decision without mutating storage."""

    mapping = _AUTHORIZATION_ACTIONS.get(event.action)
    if event.event_type != AuditEventType.DECISION or mapping is None:
        return None
    source, expected_permission = mapping
    permission_value = event.details.get(
        "permission",
        event.details.get("required_permission"),
    )
    actor_id = event.details.get("actor_id")
    actor_role = event.details.get("actor_role")
    try:
        permission = Permission(permission_value)
        decision = GovernanceDecision(event.outcome)
    except (TypeError, ValueError):
        return None
    if (
        permission != expected_permission
        or not isinstance(actor_id, str)
        or not actor_id.strip()
    ):
        return None
    if actor_role is not None and not isinstance(actor_role, str):
        return None
    trace_id = event.trace_id
    if trace_id is None and trace_store is not None:
        trace_id = trace_store.trace_id_for_incident(event.incident_id)
    return GovernanceEvent(
        event_id=event.event_id,
        timestamp=event.created_at,
        incident_id=event.incident_id,
        actor_id=actor_id,
        actor_role=actor_role,
        permission=permission,
        decision=decision,
        source=source,
        trace_id=trace_id,
    )


class GovernanceQuery:
    """Filter authorization decisions and derive governance metrics."""

    def __init__(
        self,
        audit_log: AuditStore,
        trace_store: TraceStore | None = None,
    ) -> None:
        self.audit_log = audit_log
        self.trace_store = trace_store

    def query(
        self,
        *,
        incident_id: str | None = None,
        actor_id: str | None = None,
        permission: Permission | None = None,
        decision: GovernanceDecision | None = None,
    ) -> list[GovernanceEvent]:
        events = (
            self.audit_log.list_events(incident_id, event_type=AuditEventType.DECISION)
            if incident_id is not None
            else self.audit_log.list_all_events(event_type=AuditEventType.DECISION)
        )
        projected = [
            governance
            for event in events
            if (governance := project_governance_event(event, self.trace_store))
            is not None
        ]
        return [
            event
            for event in projected
            if (actor_id is None or event.actor_id == actor_id)
            and (permission is None or event.permission == permission)
            and (decision is None or event.decision == decision)
        ]

    def metrics(self) -> AuthorizationMetrics:
        return summarize_authorization(
            (event.permission, event.decision.value) for event in self.query()
        )


__all__ = [
    "GovernanceDecision",
    "GovernanceEvent",
    "GovernanceQuery",
    "GovernanceSource",
    "project_governance_event",
]
