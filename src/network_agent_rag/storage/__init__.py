"""Pluggable persistence contracts.

Backend implementations are imported from their explicit submodules so the
default SQLite application does not eagerly load PostgreSQL or Redis clients.
"""

from network_agent_rag.storage.base import AuditStore, TraceStore

__all__ = ["AuditStore", "TraceStore"]
