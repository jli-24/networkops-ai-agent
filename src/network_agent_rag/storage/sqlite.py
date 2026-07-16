"""Lifecycle helpers for the existing SQLite storage implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.observability.store import SQLiteTraceStore


def create_sqlite_stores(
    audit_path: str | Path,
    trace_path: str | Path,
) -> tuple[SQLiteAuditLog, SQLiteTraceStore]:
    """Create the unchanged local Audit and Trace stores."""

    return SQLiteAuditLog(audit_path), SQLiteTraceStore(trace_path)


@asynccontextmanager
async def open_sqlite_checkpointer(
    path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    checkpoint = Path(path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        await saver.setup()
        yield saver


__all__ = ["create_sqlite_stores", "open_sqlite_checkpointer"]
