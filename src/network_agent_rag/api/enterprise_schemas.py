"""Pydantic contracts for incident execution and approval."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from network_agent_rag.api.schemas import QueryText, SessionId
from network_agent_rag.audit import AuditEvent
from network_agent_rag.observability import TraceEvent


IncidentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IncidentRequest(_StrictModel):
    session_id: SessionId
    query: QueryText
    incident_id: IncidentId | None = None


class ApprovalDecisionRequest(_StrictModel):
    decision: Literal["approve", "reject"]
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    plan_digest: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=64, max_length=64)
    ]
    comment: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ] | None = None


class IncidentStatusResponse(_StrictModel):
    incident_id: IncidentId
    enterprise_status: str
    risk_decision: dict[str, Any] | None
    approval_result: dict[str, Any] | None
    execution_result: dict[str, Any] | None
    answer: str
    error: str | None


class AuditResponse(_StrictModel):
    incident_id: IncidentId
    events: list[AuditEvent]


class EnterpriseTraceResponse(_StrictModel):
    incident_id: IncidentId
    events: list[TraceEvent]


__all__ = [
    "ApprovalDecisionRequest",
    "AuditResponse",
    "EnterpriseTraceResponse",
    "IncidentId",
    "IncidentRequest",
    "IncidentStatusResponse",
]
