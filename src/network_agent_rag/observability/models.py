"""Typed execution-trace contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class SpanKind(StrEnum):
    WORKFLOW = "workflow"
    AGENT = "agent"
    TOOL = "tool"
    DECISION = "decision"
    APPROVAL = "approval"
    EXECUTION = "execution"


class SpanStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TraceEventType(StrEnum):
    AGENT_ENTER = "agent_enter"
    AGENT_EXIT = "agent_exit"
    TOOL_CALL = "tool_call"
    RAG_RETRIEVAL = "rag_retrieval"
    APPROVAL_PAUSE = "approval_pause"
    RESUME = "resume"
    REPAIR_EXECUTE = "repair_execute"


class TraceEventStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    RESUMED = "resumed"


_TraceIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
_TRACE_SUMMARY_KEYS = {
    "action_count",
    "action_id",
    "argument_count",
    "attempt",
    "decision",
    "document_count",
    "error_type",
    "execution_status",
    "relevance_score",
    "result_count",
    "risk_level",
    "status",
    "target_sha256",
    "tool_name",
}
_TRACE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TRACE_COUNT_KEYS = {
    "action_count",
    "argument_count",
    "attempt",
    "document_count",
    "result_count",
}


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: AwareDatetime
    incident_id: _TraceIdentifier
    trace_id: _TraceIdentifier
    run_id: _TraceIdentifier
    agent_name: _TraceIdentifier
    node_name: _TraceIdentifier
    event_type: TraceEventType
    input: dict[str, object] | None = None
    output: dict[str, object] | None = None
    latency: float | None = Field(default=None, ge=0)
    status: TraceEventStatus

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_validator("input", "output")
    @classmethod
    def validate_payload_summary(
        cls, value: dict[str, object] | None
    ) -> dict[str, object] | None:
        if value is None:
            return None
        allowed = {"summary", "sha256", "field_count", "item_count"}
        if set(value) - allowed:
            raise ValueError("trace payload may contain only sanitized summary fields")
        summary = value.get("summary")
        digest = value.get("sha256")
        if not isinstance(summary, dict) or not all(
            isinstance(key, str)
            and (item is None or isinstance(item, (str, int, float, bool)))
            for key, item in summary.items()
        ):
            raise ValueError("trace payload summary must contain scalar values")
        if set(summary) - _TRACE_SUMMARY_KEYS:
            raise ValueError("trace payload contains an unsupported summary field")
        for key, item in summary.items():
            if key in _TRACE_COUNT_KEYS:
                if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                    raise ValueError(f"trace summary {key} must be a non-negative integer")
            elif key == "relevance_score":
                if (
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not 0 <= item <= 1
                ):
                    raise ValueError("trace summary relevance_score must be between 0 and 1")
            elif not isinstance(item, str):
                raise ValueError(f"trace summary {key} must be a short identifier")
            elif key == "target_sha256":
                if len(item) != 64 or any(
                    character not in "0123456789abcdef" for character in item
                ):
                    raise ValueError("target_sha256 must be a lowercase SHA-256 digest")
            elif _TRACE_IDENTIFIER_PATTERN.fullmatch(item) is None:
                raise ValueError(f"trace summary {key} must be a short identifier")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("trace payload sha256 must be a lowercase SHA-256 digest")
        for count_name in ("field_count", "item_count"):
            count = value.get(count_name)
            if count is not None and (
                not isinstance(count, int) or isinstance(count, bool) or count < 0
            ):
                raise ValueError(f"trace payload {count_name} must be non-negative")
        return value


class TraceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    span_id: str
    trace_id: str
    run_id: str
    incident_id: str
    parent_span_id: str | None = None
    kind: SpanKind
    name: str
    status: SpanStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    attempt: int = Field(ge=1)
    error_code: str | None = None
    input_summary_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_summary_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attributes: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "TraceSpan":
        complete = self.status != SpanStatus.RUNNING
        if complete != (self.ended_at is not None and self.duration_ms is not None):
            raise ValueError("completed spans require ended_at and duration_ms")
        return self


class IncidentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: str
    trace_id: str
    status: SpanStatus
    risk_level: str | None = None
    started_at: AwareDatetime
    updated_at: AwareDatetime
    run_count: int = Field(ge=1)
    span_count: int = Field(ge=1)


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    incident_id: str
    source: Literal["trace", "audit"]
    event_type: str
    name: str
    status: str
    timestamp: AwareDatetime
    duration_ms: float | None = Field(default=None, ge=0)
    details: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "IncidentSummary",
    "SpanKind",
    "SpanStatus",
    "TimelineEvent",
    "TraceEvent",
    "TraceEventStatus",
    "TraceEventType",
    "TraceSpan",
]
