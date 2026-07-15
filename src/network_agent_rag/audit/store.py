"""Small append-only SQLite audit repository."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from collections.abc import Iterator
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from network_agent_rag.audit.models import AuditEvent, AuditEventType


_REDACTED_KEYS = {"api_key", "authorization", "password", "secret", "token"}


class SQLiteAuditLog:
    """Persist redacted workflow audit events in a local SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    incident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT,
                    UNIQUE (incident_id, idempotency_key)
                )
                """
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
        kind = AuditEventType(event_type)
        event = AuditEvent(
            event_id=uuid4().hex,
            incident_id=_required(incident_id, "incident_id"),
            event_type=kind,
            actor=_required(actor, "actor"),
            action=_required(action, "action"),
            outcome=_required(outcome, "outcome"),
            details=_redact(details or {}),
            created_at=datetime.now(timezone.utc),
            idempotency_key=idempotency_key,
        )
        encoded = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connection() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    """SELECT event_id, incident_id, event_type, actor, action,
                              outcome, details, created_at, idempotency_key
                       FROM audit_events
                       WHERE incident_id = ? AND idempotency_key = ?""",
                    (event.incident_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return _event_from_row(existing)
            connection.execute(
                """INSERT INTO audit_events (
                       event_id, incident_id, event_type, actor, action,
                       outcome, details, created_at, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.incident_id,
                    event.event_type.value,
                    event.actor,
                    event.action,
                    event.outcome,
                    encoded,
                    event.created_at.isoformat(),
                    event.idempotency_key,
                ),
            )
        return event

    def list_events(
        self,
        incident_id: str,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]:
        query = """SELECT event_id, incident_id, event_type, actor, action,
                          outcome, details, created_at, idempotency_key
                   FROM audit_events WHERE incident_id = ?"""
        values: list[object] = [_required(incident_id, "incident_id")]
        if event_type is not None:
            query += " AND event_type = ?"
            values.append(AuditEventType(event_type).value)
        query += " ORDER BY sequence"
        with self._lock, self._connection() as connection:
            return [
                _event_from_row(row)
                for row in connection.execute(query, values).fetchall()
            ]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
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


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "***REDACTED***"
            if str(key).lower() in _REDACTED_KEYS
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("audit details must contain only JSON-compatible values")


def _event_from_row(row: tuple[object, ...]) -> AuditEvent:
    return AuditEvent(
        event_id=row[0],
        incident_id=row[1],
        event_type=row[2],
        actor=row[3],
        action=row[4],
        outcome=row[5],
        details=json.loads(str(row[6])),
        created_at=datetime.fromisoformat(str(row[7])),
        idempotency_key=row[8],
    )


__all__ = ["SQLiteAuditLog"]
