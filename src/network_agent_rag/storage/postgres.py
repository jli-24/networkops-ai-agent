"""PostgreSQL Audit, Trace, and LangGraph checkpoint adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from network_agent_rag.audit.models import AuditEvent, AuditEventType
from network_agent_rag.audit.store import redact_sensitive
from network_agent_rag.observability.models import (
    IncidentSummary,
    SpanKind,
    SpanStatus,
    TraceSpan,
)


_AUDIT_COLUMNS = """event_id, incident_id, event_type, actor, action, outcome,
                     details, created_at, idempotency_key"""
_TRACE_COLUMNS = """span_id, trace_id, run_id, incident_id, parent_span_id,
                     kind, name, status, started_at, ended_at, duration_ms,
                     attempt, error_code, input_summary_hash, output_summary_hash,
                     attributes, idempotency_key"""


class PostgreSQLAuditStore:
    """Synchronous PostgreSQL implementation of the AuditStore protocol."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def setup(self) -> None:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS networkops_audit_events (
                    sequence BIGSERIAL PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    incident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE (incident_id, idempotency_key)
                )
                """
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS networkops_audit_incident_sequence
                   ON networkops_audit_events (incident_id, sequence)"""
            )

    def record(
        self,
        *,
        incident_id: str,
        event_type: AuditEventType | str,
        actor: str,
        action: str,
        outcome: str,
        details: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=uuid4().hex,
            incident_id=_required(incident_id, "incident_id"),
            event_type=AuditEventType(event_type),
            actor=_required(actor, "actor"),
            action=_required(action, "action"),
            outcome=_required(outcome, "outcome"),
            details=redact_sensitive(details or {}),
            created_at=datetime.now(timezone.utc),
            idempotency_key=idempotency_key,
        )
        with self.pool.connection() as connection, connection.cursor() as cursor:
            insert = """INSERT INTO networkops_audit_events (
                       event_id, incident_id, event_type, actor, action, outcome,
                       details, created_at, idempotency_key
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            values = (
                event.event_id,
                event.incident_id,
                event.event_type.value,
                event.actor,
                event.action,
                event.outcome,
                Jsonb(event.details),
                event.created_at,
                event.idempotency_key,
            )
            if idempotency_key is None:
                cursor.execute(insert, values)
                return event
            cursor.execute(
                f"""{insert}
                    ON CONFLICT (incident_id, idempotency_key) DO NOTHING
                    RETURNING {_AUDIT_COLUMNS}""",
                values,
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return _audit_from_row(inserted)
            cursor.execute(
                f"""SELECT {_AUDIT_COLUMNS} FROM networkops_audit_events
                    WHERE incident_id = %s AND idempotency_key = %s""",
                (event.incident_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise RuntimeError("idempotent audit insert did not return a record")
            return _audit_from_row(existing)

    def list_events(
        self,
        incident_id: str,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]:
        query = f"""SELECT {_AUDIT_COLUMNS} FROM networkops_audit_events
                    WHERE incident_id = %s"""
        values: list[object] = [_required(incident_id, "incident_id")]
        if event_type is not None:
            query += " AND event_type = %s"
            values.append(AuditEventType(event_type).value)
        query += " ORDER BY sequence"
        return self._query_events(query, values)

    def list_all_events(
        self,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]:
        query = f"SELECT {_AUDIT_COLUMNS} FROM networkops_audit_events"
        values: list[object] = []
        if event_type is not None:
            query += " WHERE event_type = %s"
            values.append(AuditEventType(event_type).value)
        query += " ORDER BY sequence"
        return self._query_events(query, values)

    def _query_events(self, query: str, values: list[object]) -> list[AuditEvent]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(query, values)
            return [_audit_from_row(row) for row in cursor.fetchall()]


class PostgreSQLTraceStore:
    """Synchronous PostgreSQL implementation of the TraceStore protocol."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def setup(self) -> None:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS networkops_trace_spans (
                    sequence BIGSERIAL PRIMARY KEY,
                    span_id TEXT NOT NULL UNIQUE,
                    trace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ,
                    duration_ms DOUBLE PRECISION,
                    attempt INTEGER NOT NULL,
                    error_code TEXT,
                    input_summary_hash TEXT,
                    output_summary_hash TEXT,
                    attributes JSONB NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE (incident_id, idempotency_key)
                )
                """
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS networkops_trace_incident_sequence
                   ON networkops_trace_spans (incident_id, sequence)"""
            )

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
        required = {
            "trace_id": _required(trace_id, "trace_id"),
            "run_id": _required(run_id, "run_id"),
            "incident_id": _required(incident_id, "incident_id"),
            "name": _required(name, "name"),
        }
        started = _aware(started_at or datetime.now(timezone.utc), "started_at")
        span_kind = SpanKind(kind)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            if idempotency_key is not None:
                cursor.execute(
                    f"""SELECT {_TRACE_COLUMNS} FROM networkops_trace_spans
                        WHERE incident_id = %s AND idempotency_key = %s""",
                    (required["incident_id"], idempotency_key),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return _span_from_row(existing)
            if parent_span_id is not None:
                cursor.execute(
                    """SELECT trace_id, run_id, incident_id
                       FROM networkops_trace_spans WHERE span_id = %s""",
                    (parent_span_id,),
                )
                parent = cursor.fetchone()
                expected = (
                    required["trace_id"],
                    required["run_id"],
                    required["incident_id"],
                )
                if parent is None or _parent_identity(parent) != expected:
                    raise ValueError(
                        "parent span must belong to the same trace, run, and incident"
                    )
            attempt_lock_key = json.dumps(
                [
                    required["incident_id"],
                    required["run_id"],
                    span_kind.value,
                    required["name"],
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (attempt_lock_key,),
            )
            cursor.execute(
                """SELECT COUNT(*) AS attempt_count FROM networkops_trace_spans
                   WHERE incident_id = %s AND run_id = %s AND kind = %s AND name = %s""",
                (
                    required["incident_id"],
                    required["run_id"],
                    span_kind.value,
                    required["name"],
                ),
            )
            count_row = cursor.fetchone()
            attempt = _count_value(count_row) + 1
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
            insert = """INSERT INTO networkops_trace_spans (
                       span_id, trace_id, run_id, incident_id, parent_span_id,
                       kind, name, status, started_at, ended_at, duration_ms,
                       attempt, error_code, input_summary_hash, output_summary_hash,
                       attributes, idempotency_key
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                             %s, %s, %s, %s, %s, %s)"""
            if idempotency_key is None:
                cursor.execute(insert, _span_values(span))
                return span
            cursor.execute(
                f"""{insert}
                    ON CONFLICT (incident_id, idempotency_key) DO NOTHING
                    RETURNING {_TRACE_COLUMNS}""",
                _span_values(span),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return _span_from_row(inserted)
            cursor.execute(
                f"""SELECT {_TRACE_COLUMNS} FROM networkops_trace_spans
                    WHERE incident_id = %s AND idempotency_key = %s""",
                (required["incident_id"], idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise RuntimeError("idempotent trace insert did not return a record")
            return _span_from_row(existing)

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
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT {_TRACE_COLUMNS} FROM networkops_trace_spans
                    WHERE span_id = %s FOR UPDATE""",
                (_required(span_id, "span_id"),),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"unknown span: {span_id}")
            current = _span_from_row(row)
            if current.status != SpanStatus.RUNNING:
                return current
            if ended < current.started_at:
                raise ValueError("ended_at cannot be before started_at")
            merged = {**current.attributes, **redact_sensitive(attributes or {})}
            finished = current.model_copy(
                update={
                    "status": final_status,
                    "ended_at": ended,
                    "duration_ms": (ended - current.started_at).total_seconds() * 1000,
                    "error_code": error_code,
                    "output_summary_hash": output_summary_hash,
                    "attributes": merged,
                }
            )
            cursor.execute(
                """UPDATE networkops_trace_spans
                   SET status = %s, ended_at = %s, duration_ms = %s,
                       error_code = %s, output_summary_hash = %s, attributes = %s
                   WHERE span_id = %s""",
                (
                    finished.status.value,
                    finished.ended_at,
                    finished.duration_ms,
                    finished.error_code,
                    finished.output_summary_hash,
                    Jsonb(finished.attributes),
                    finished.span_id,
                ),
            )
        return TraceSpan.model_validate(finished.model_dump())

    def list_spans(
        self,
        incident_id: str,
        *,
        run_id: str | None = None,
    ) -> list[TraceSpan]:
        query = f"""SELECT {_TRACE_COLUMNS} FROM networkops_trace_spans
                    WHERE incident_id = %s"""
        values: list[object] = [_required(incident_id, "incident_id")]
        if run_id is not None:
            query += " AND run_id = %s"
            values.append(_required(run_id, "run_id"))
        query += " ORDER BY sequence"
        return self._query_spans(query, values)

    def trace_id_for_incident(self, incident_id: str) -> str | None:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT trace_id FROM networkops_trace_spans
                   WHERE incident_id = %s ORDER BY sequence LIMIT 1""",
                (_required(incident_id, "incident_id"),),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return str(row["trace_id"] if isinstance(row, dict) else row[0])

    def list_all_spans(self) -> list[TraceSpan]:
        return self._query_spans(
            f"SELECT {_TRACE_COLUMNS} FROM networkops_trace_spans ORDER BY sequence",
            [],
        )

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
            workflows = [item for item in spans if item.kind == SpanKind.WORKFLOW]
            if not workflows:
                continue
            latest = workflows[-1]
            risk = next(
                (
                    str(item.attributes["risk_level"])
                    for item in reversed(spans)
                    if item.attributes.get("risk_level") is not None
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
                    run_count=len({item.run_id for item in workflows}),
                    span_count=len(spans),
                )
            )
        summaries.sort(key=lambda item: (item.started_at, item.incident_id))
        page = summaries[offset : offset + limit]
        next_offset = offset + len(page)
        return page, str(next_offset) if next_offset < len(summaries) else None

    def _query_spans(self, query: str, values: list[object]) -> list[TraceSpan]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(query, values)
            return [_span_from_row(row) for row in cursor.fetchall()]


@contextmanager
def open_postgres_stores(
    database_url: str,
    *,
    pool_factory: Callable[..., Any] = ConnectionPool,
) -> Iterator[tuple[PostgreSQLAuditStore, PostgreSQLTraceStore]]:
    """Own one pool while keeping Audit and Trace in independent tables."""

    url = _required(database_url, "DATABASE_URL")
    pool = pool_factory(conninfo=url, kwargs={"row_factory": dict_row}, open=False)
    try:
        pool.open(wait=True)
        audit = PostgreSQLAuditStore(pool)
        trace = PostgreSQLTraceStore(pool)
        audit.setup()
        trace.setup()
        yield audit, trace
    finally:
        pool.close()


@asynccontextmanager
async def open_postgres_checkpointer(
    database_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    url = _required(database_url, "DATABASE_URL")
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
    return value


def _audit_from_row(row: Any) -> AuditEvent:
    return AuditEvent(
        event_id=row["event_id"],
        incident_id=row["incident_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        action=row["action"],
        outcome=row["outcome"],
        details=row["details"],
        created_at=row["created_at"],
        idempotency_key=row["idempotency_key"],
    )


def _span_from_row(row: Any) -> TraceSpan:
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        run_id=row["run_id"],
        incident_id=row["incident_id"],
        parent_span_id=row["parent_span_id"],
        kind=row["kind"],
        name=row["name"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_ms=row["duration_ms"],
        attempt=row["attempt"],
        error_code=row["error_code"],
        input_summary_hash=row["input_summary_hash"],
        output_summary_hash=row["output_summary_hash"],
        attributes=row["attributes"],
        idempotency_key=row["idempotency_key"],
    )


def _parent_identity(row: Any) -> tuple[str, str, str]:
    if isinstance(row, dict):
        return str(row["trace_id"]), str(row["run_id"]), str(row["incident_id"])
    return str(row[0]), str(row[1]), str(row[2])


def _count_value(row: Any) -> int:
    if row is None:
        return 0
    return int(row["attempt_count"] if isinstance(row, dict) else row[0])


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
        span.started_at,
        span.ended_at,
        span.duration_ms,
        span.attempt,
        span.error_code,
        span.input_summary_hash,
        span.output_summary_hash,
        Jsonb(span.attributes),
        span.idempotency_key,
    )


__all__ = [
    "PostgreSQLAuditStore",
    "PostgreSQLTraceStore",
    "open_postgres_checkpointer",
    "open_postgres_stores",
]
