"""Process-local conversation history storage."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

from network_agent_rag.packs.networkops.api.schemas import HistoryMessage


class InMemoryHistoryStore:
    """Thread-safe, process-local chat history grouped by session id."""

    def __init__(self) -> None:
        self._messages: dict[str, list[HistoryMessage]] = {}
        self._lock = Lock()

    def append_exchange(self, session_id: str, query: str, answer: str) -> None:
        user_message = HistoryMessage(
            role="user",
            content=query,
            created_at=datetime.now(UTC),
        )
        assistant_message = HistoryMessage(
            role="assistant",
            content=answer,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._messages.setdefault(session_id, []).extend(
                (user_message, assistant_message)
            )

    def get(self, session_id: str) -> list[HistoryMessage]:
        with self._lock:
            return [
                message.model_copy(deep=True)
                for message in self._messages.get(session_id, ())
            ]


__all__ = ["InMemoryHistoryStore"]
