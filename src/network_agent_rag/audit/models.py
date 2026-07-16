"""Typed audit records for enterprise workflow activity."""

from __future__ import annotations

from enum import StrEnum

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator


class AuditEventType(StrEnum):
    AGENT_CALL = "agent_call"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    APPROVAL = "approval"
    TRACE = "trace"


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    incident_id: str
    event_type: AuditEventType
    actor: str
    action: str
    outcome: str
    details: dict[str, object]
    created_at: AwareDatetime
    idempotency_key: str | None = None
    actor_id: str | None = None
    actor_role: str | None = None

    @model_validator(mode="before")
    @classmethod
    def project_actor_details(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        details = values.get("details")
        if not isinstance(details, dict):
            return values
        projected = dict(values)
        for field in ("actor_id", "actor_role"):
            value = details.get(field)
            if field not in projected and isinstance(value, str):
                projected[field] = value
        return projected

    @property
    def trace_id(self) -> str | None:
        """Return a stored trace reference without changing the audit schema."""

        direct = self.details.get("trace_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        trace_event = self.details.get("trace_event")
        if isinstance(trace_event, dict):
            nested = trace_event.get("trace_id")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        return None


__all__ = ["AuditEvent", "AuditEventType"]
