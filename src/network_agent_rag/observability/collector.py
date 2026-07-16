"""Audit-backed collector for sanitized enterprise TraceEvent records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock
from typing import TypeVar

from network_agent_rag.audit import AuditEventType
from network_agent_rag.audit.store import is_sensitive_key, redact_sensitive
from network_agent_rag.observability.models import (
    TraceEvent,
    TraceEventStatus,
    TraceEventType,
)
from network_agent_rag.observability.trace import load_trace_events
from network_agent_rag.storage.base import AuditStore


_Result = TypeVar("_Result")
_SAFE_SCALARS = {
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
_COUNT_KEYS = {
    "action_count",
    "argument_count",
    "attempt",
    "document_count",
    "result_count",
}
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class TracePersistenceError(RuntimeError):
    """Raised when a required TraceEvent cannot be appended to Audit."""


@dataclass(frozen=True)
class _TraceToken:
    incident_id: str
    trace_id: str
    run_id: str
    agent_name: str
    node_name: str
    started_at: datetime
    attempt: int


class TraceCollector:
    """Append sanitized lifecycle events to the existing Audit log."""

    def __init__(
        self,
        audit_log: AuditStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.audit_log = audit_log
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._attempts: dict[tuple[str, str, str, str], int] = {}
        self._attempt_lock = RLock()

    def agent_enter(
        self,
        *,
        incident_id: str,
        trace_id: str,
        run_id: str,
        agent_name: str,
        node_name: str,
        input_data: object = None,
        attempt: int | None = None,
    ) -> _TraceToken:
        started_at = _aware(self.clock())
        actual_attempt = self._attempt(
            incident_id, run_id, node_name, TraceEventType.AGENT_ENTER, attempt
        )
        token = _TraceToken(
            incident_id=incident_id,
            trace_id=trace_id,
            run_id=run_id,
            agent_name=agent_name,
            node_name=node_name,
            started_at=started_at,
            attempt=actual_attempt,
        )
        self._record(
            TraceEvent(
                timestamp=started_at,
                incident_id=incident_id,
                trace_id=trace_id,
                run_id=run_id,
                agent_name=agent_name,
                node_name=node_name,
                event_type=TraceEventType.AGENT_ENTER,
                input=_summarize(input_data),
                status=TraceEventStatus.RUNNING,
            ),
            actual_attempt,
        )
        return token

    def agent_exit(
        self,
        token: _TraceToken,
        *,
        status: TraceEventStatus | str,
        output_data: object = None,
    ) -> TraceEvent:
        ended_at = _aware(self.clock())
        latency = _latency_ms(token.started_at, ended_at)
        return self._record(
            TraceEvent(
                timestamp=ended_at,
                incident_id=token.incident_id,
                trace_id=token.trace_id,
                run_id=token.run_id,
                agent_name=token.agent_name,
                node_name=token.node_name,
                event_type=TraceEventType.AGENT_EXIT,
                output=_summarize(output_data),
                latency=latency,
                status=status,
            ),
            token.attempt,
        )

    def record_call(
        self,
        *,
        incident_id: str,
        trace_id: str,
        run_id: str,
        agent_name: str,
        node_name: str,
        event_type: TraceEventType | str,
        operation: Callable[[], _Result],
        input_data: object = None,
        output_builder: Callable[[_Result], object] | None = None,
        attempt: int | None = None,
    ) -> _Result:
        kind = TraceEventType(event_type)
        if kind not in {TraceEventType.TOOL_CALL, TraceEventType.RAG_RETRIEVAL}:
            raise ValueError("record_call only accepts tool_call or rag_retrieval")
        actual_attempt = self._attempt(
            incident_id, run_id, node_name, kind, attempt
        )
        started_at = _aware(self.clock())
        try:
            result = operation()
        except Exception as error:
            ended_at = _aware(self.clock())
            self._record(
                TraceEvent(
                    timestamp=ended_at,
                    incident_id=incident_id,
                    trace_id=trace_id,
                    run_id=run_id,
                    agent_name=agent_name,
                    node_name=node_name,
                    event_type=kind,
                    input=_summarize(input_data),
                    output=_summarize({"error_type": type(error).__name__}),
                    latency=_latency_ms(started_at, ended_at),
                    status=TraceEventStatus.FAILED,
                ),
                actual_attempt,
            )
            raise
        ended_at = _aware(self.clock())
        output_data = output_builder(result) if output_builder is not None else result
        self._record(
            TraceEvent(
                timestamp=ended_at,
                incident_id=incident_id,
                trace_id=trace_id,
                run_id=run_id,
                agent_name=agent_name,
                node_name=node_name,
                event_type=kind,
                input=_summarize(input_data),
                output=_summarize(output_data),
                latency=_latency_ms(started_at, ended_at),
                status=TraceEventStatus.SUCCEEDED,
            ),
            actual_attempt,
        )
        return result

    def approval_pause(self, **values: object) -> TraceEvent:
        return self._instant(
            TraceEventType.APPROVAL_PAUSE,
            TraceEventStatus.INTERRUPTED,
            **values,
        )

    def resume(self, **values: object) -> TraceEvent:
        return self._instant(
            TraceEventType.RESUME,
            TraceEventStatus.RESUMED,
            **values,
        )

    def repair_execute(self, **values: object) -> TraceEvent:
        return self._instant(
            TraceEventType.REPAIR_EXECUTE,
            TraceEventStatus.RUNNING,
            **values,
        )

    def _instant(
        self,
        event_type: TraceEventType,
        status: TraceEventStatus,
        *,
        incident_id: object,
        trace_id: object,
        run_id: object,
        agent_name: object,
        node_name: object,
        input_data: object = None,
        attempt: object = None,
    ) -> TraceEvent:
        if attempt is not None and (
            not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1
        ):
            raise ValueError("attempt must be a positive integer")
        actual_attempt = self._attempt(
            str(incident_id), str(run_id), str(node_name), event_type, attempt
        )
        event = TraceEvent(
            timestamp=_aware(self.clock()),
            incident_id=incident_id,
            trace_id=trace_id,
            run_id=run_id,
            agent_name=agent_name,
            node_name=node_name,
            event_type=event_type,
            input=_summarize(input_data),
            status=status,
        )
        return self._record(event, actual_attempt)

    def _attempt(
        self,
        incident_id: str,
        run_id: str,
        node_name: str,
        event_type: TraceEventType,
        requested: int | None,
    ) -> int:
        if requested is not None:
            if isinstance(requested, bool) or requested < 1:
                raise ValueError("attempt must be a positive integer")
            return requested
        key = (incident_id, run_id, node_name, event_type.value)
        with self._attempt_lock:
            if key not in self._attempts:
                self._attempts[key] = sum(
                    event.run_id == run_id
                    and event.node_name == node_name
                    and event.event_type == event_type
                    for event in load_trace_events(self.audit_log, incident_id)
                )
            self._attempts[key] += 1
            return self._attempts[key]

    def _record(self, event: TraceEvent, attempt: int) -> TraceEvent:
        try:
            self.audit_log.record(
                incident_id=event.incident_id,
                event_type=AuditEventType.TRACE,
                actor=event.agent_name,
                action=event.event_type.value,
                outcome=event.status.value,
                details={"trace_event": event.model_dump(mode="json")},
                idempotency_key=(
                    f"trace:{event.run_id}:{event.node_name}:"
                    f"{event.event_type.value}:{attempt}"
                ),
            )
        except Exception as error:
            raise TracePersistenceError("TraceEvent persistence failed") from error
        return event


def _summarize(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    summary: dict[str, object] = {}
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            if is_sensitive_key(key) or key not in _SAFE_SCALARS:
                continue
            if key in _COUNT_KEYS:
                if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                    summary[key] = item
            elif key == "relevance_score":
                if (
                    isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    and 0 <= item <= 1
                ):
                    summary[key] = item
            elif isinstance(item, str) and _IDENTIFIER_PATTERN.fullmatch(item):
                summary[key] = item
    if isinstance(value, dict) and "target" in value:
        summary["target_sha256"] = _sha256(_digestable(value["target"]))
    result: dict[str, object] = {
        "summary": summary,
        "sha256": _sha256(_digestable(value)),
    }
    if isinstance(value, dict):
        result["field_count"] = sum(
            not is_sensitive_key(key) for key in value
        )
    elif isinstance(value, (list, tuple)):
        result["item_count"] = len(value)
    return result


def _digestable(value: object) -> object:
    return redact_sensitive(_json_safe(value), include_content=False)


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__}


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trace clock must return a timezone-aware datetime")
    return value


def _latency_ms(started_at: datetime, ended_at: datetime) -> float:
    if ended_at < started_at:
        raise ValueError("trace clock cannot move backwards")
    return round((ended_at - started_at).total_seconds() * 1000, 3)


__all__ = ["TraceCollector", "TracePersistenceError"]
