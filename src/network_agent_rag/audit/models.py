"""Typed audit records for enterprise workflow activity."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict


class AuditEventType(StrEnum):
    AGENT_CALL = "agent_call"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    APPROVAL = "approval"


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


__all__ = ["AuditEvent", "AuditEventType"]
