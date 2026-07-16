"""Small SQLite execution-trace repository."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from network_agent_rag.audit.store import redact_sensitive
from network_agent_rag.observability.models import (
    IncidentSummary,
    SpanKind,
    SpanStatus,
    TraceSpan,
)


class SQLiteTraceStore:
    """Persist low-volume, redacted trace spans for the local demo backend."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_spans (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    span_id TEXT NOT NULL UNIQUE,
                    trace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_ms REAL,
                    attempt INTEGER NOT NULL,
                    error_code TEXT,
                    input_summary_hash TEXT,
                    output_summary_hash TEXT,
                    attributes TEXT NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE (incident_id, idempotency_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS trace_spans_incident_sequence "
                "ON trace_spans (incident_id, sequence)"
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(trace_spans)")
            }
            for column in ("input_summary_hash", "output_summary_hash"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE trace_spans ADD COLUMN {column} TEXT")

    def start_span(
        self,
        *,
        trace_id: str,
        run_id: str,
        incident_id: str,
        kind: SpanKind | str,
        name: str,
        parent_span_id: str | None = None,
        started_at: datetime | None = None,
        attributes: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        input_summary_hash: str | None = None,
    ) -> TraceSpan:
        started = _aware(started_at or datetime.now(timezone.utc), "started_at")
        required = {
            "trace_id": _required(trace_id, "trace_id"),
            "run_id": _required(run_id, "run_id"),
            "incident_id": _required(incident_id, "incident_id"),
            "name": _required(name, "name"),
        }
        span_kind = SpanKind(kind)
        with self._lock, self._connection() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM trace_spans WHERE incident_id = ? AND idempotency_key = ?",
                    (required["incident_id"], idempotency_key),
                ).fetchone()
                if existing is not None:
                    return _span_from_row(existing)
            if parent_span_id is not None:
                parent = connection.execute(
                    "SELECT trace_id, run_id, incident_id FROM trace_spans WHERE span_id = ?",
                    (parent_span_id,),
                ).fetchone()
                if parent is None or tuple(parent) != (
                    required["trace_id"],
                    required["run_id"],
                    required["incident_id"],
                ):
                    raise ValueError("parent span must belong to the same trace, run, and incident")
            attempt = connection.execute(
                """SELECT COUNT(*) FROM trace_spans
                   WHERE incident_id = ? AND run_id = ? AND kind = ? AND name = ?""",
                (
                    required["incident_id"],
                    required["run_id"],
                    span_kind.value,
                    required["name"],
                ),
            ).fetchone()[0] + 1
            span = TraceSpan(
                span_id=uuid4().hex,
                **required,
                parent_span_id=parent_span_id,
                kind=span_kind,
                status=SpanStatus.RUNNING,
                started_at=started,
                attempt=attempt,
                input_summary_hash=input_summary_hash,
                attributes=redact_sensitive(attributes or {}),
                idempotency_key=idempotency_key,
            )
            connection.execute(
                """INSERT INTO trace_spans (
                       span_id, trace_id, run_id, incident_id, parent_span_id,
                       kind, name, status, started_at, ended_at, duration_ms,
                       attempt, error_code, input_summary_hash, output_summary_hash,
                       attributes, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _span_values(span),
            )
        return span

    def finish_span(
        self,
        span_id: str,
        *,
        status: SpanStatus | str,
        ended_at: datetime | None = None,
        error_code: str | None = None,
        attributes: dict[str, object] | None = None,
        output_summary_hash: str | None = None,
    ) -> TraceSpan:
        final_status = SpanStatus(status)
        if final_status == SpanStatus.RUNNING:
            raise ValueError("finished span status cannot be running")
        ended = _aware(ended_at or datetime.now(timezone.utc), "ended_at")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM trace_spans WHERE span_id = ?", (span_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown span: {span_id}")
            current = _span_from_row(row)
            if current.status != SpanStatus.RUNNING:
                return current
            if ended < current.started_at:
                raise ValueError("ended_at cannot be before started_at")
            merged = {**current.attributes, **redact_sensitive(attributes or {})}
            duration = (ended - current.started_at).total_seconds() * 1000
            connection.execute(
                """UPDATE trace_spans
                   SET status = ?, ended_at = ?, duration_ms = ?, error_code = ?,
                       output_summary_hash = ?, attributes = ?
                   WHERE span_id = ?""",
                (
                    final_status.value,
                    ended.isoformat(),
                    duration,
                    error_code,
                    output_summary_hash,
                    json.dumps(merged, ensure_ascii=False, sort_keys=True),
                    span_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM trace_spans WHERE span_id = ?", (span_id,)
            ).fetchone()
        return _span_from_row(updated)

    def list_spans(self, incident_id: str, *, run_id: str | None = None) -> list[TraceSpan]:
        query = "SELECT * FROM trace_spans WHERE incident_id = ?"
        values: list[object] = [_required(incident_id, "incident_id")]
        if run_id is not None:
            query += " AND run_id = ?"
            values.append(_required(run_id, "run_id"))
        query += " ORDER BY sequence"
        with self._lock, self._connection() as connection:
            return [_span_from_row(row) for row in connection.execute(query, values)]

    def trace_id_for_incident(self, incident_id: str) -> str | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT trace_id FROM trace_spans WHERE incident_id = ? ORDER BY sequence LIMIT 1",
                (_required(incident_id, "incident_id"),),
            ).fetchone()
        return None if row is None else str(row[0])

    def list_all_spans(self) -> list[TraceSpan]:
        with self._lock, self._connection() as connection:
            return [_span_from_row(row) for row in connection.execute(
                "SELECT * FROM trace_spans ORDER BY sequence"
            )]

    def list_incidents(
        self,
        *,
        status: SpanStatus | str | None = None,
        risk_level: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[IncidentSummary], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            offset = int(cursor) if cursor is not None else 0
        except ValueError as error:
            raise ValueError("cursor must be a non-negative integer") from error
        if offset < 0:
            raise ValueError("cursor must be a non-negative integer")
        wanted_status = SpanStatus(status) if status is not None else None
        groups: dict[str, list[TraceSpan]] = {}
        for span in self.list_all_spans():
            groups.setdefault(span.incident_id, []).append(span)
        summaries: list[IncidentSummary] = []
        for incident_id, spans in groups.items():
            workflows = [span for span in spans if span.kind == SpanKind.WORKFLOW]
            if not workflows:
                continue
            latest = workflows[-1]
            risk = next(
                (
                    str(span.attributes["risk_level"])
                    for span in reversed(spans)
                    if span.attributes.get("risk_level") is not None
                ),
                None,
            )
            if wanted_status is not None and latest.status != wanted_status:
                continue
            if risk_level is not None and risk != risk_level:
                continue
            summaries.append(
                IncidentSummary(
                    incident_id=incident_id,
                    trace_id=latest.trace_id,
                    status=latest.status,
                    risk_level=risk,
                    started_at=workflows[0].started_at,
                    updated_at=latest.ended_at or latest.started_at,
                    run_count=len({span.run_id for span in workflows}),
                    span_count=len(spans),
                )
            )
        summaries.sort(key=lambda item: (item.started_at, item.incident_id))
        page = summaries[offset : offset + limit]
        next_offset = offset + len(page)
        return page, str(next_offset) if next_offset < len(summaries) else None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                yield connection
        finally:
            connection.close()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
    return value


def _span_values(span: TraceSpan) -> tuple[object, ...]:
    return (
        span.span_id,
        span.trace_id,
        span.run_id,
        span.incident_id,
        span.parent_span_id,
        span.kind.value,
        span.name,
        span.status.value,
        span.started_at.isoformat(),
        span.ended_at.isoformat() if span.ended_at else None,
        span.duration_ms,
        span.attempt,
        span.error_code,
        span.input_summary_hash,
        span.output_summary_hash,
        json.dumps(span.attributes, ensure_ascii=False, sort_keys=True),
        span.idempotency_key,
    )


def _span_from_row(row: sqlite3.Row) -> TraceSpan:
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        run_id=row["run_id"],
        incident_id=row["incident_id"],
        parent_span_id=row["parent_span_id"],
        kind=row["kind"],
        name=row["name"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        duration_ms=row["duration_ms"],
        attempt=row["attempt"],
        error_code=row["error_code"],
        input_summary_hash=row["input_summary_hash"],
        output_summary_hash=row["output_summary_hash"],
        attributes=json.loads(row["attributes"]),
        idempotency_key=row["idempotency_key"],
    )


__all__ = ["SQLiteTraceStore"]
