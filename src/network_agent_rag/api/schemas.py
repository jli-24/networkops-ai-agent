"""Pydantic contracts for streaming chat and conversation history."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from network_agent_rag.agents import Intent


SessionId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]
QueryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChatRequest(_StrictModel):
    session_id: SessionId
    query: QueryText


class SourceDocument(_StrictModel):
    page_content: str
    metadata: dict[str, Any]


class ChatResponse(_StrictModel):
    session_id: SessionId
    answer: str
    intent: Intent
    rewritten_query: str
    relevance_score: float | None
    source_documents: list[SourceDocument]
    topology_context: dict[str, Any]
    metrics: dict[str, Any]
    iteration: int
    error: str | None


class NodeProgress(_StrictModel):
    session_id: SessionId
    node: str
    iteration: int | None


class StreamError(_StrictModel):
    session_id: SessionId
    code: str
    message: str


class HistoryMessage(_StrictModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class HistoryResponse(_StrictModel):
    session_id: SessionId
    messages: list[HistoryMessage]


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "HistoryMessage",
    "HistoryResponse",
    "NodeProgress",
    "QueryText",
    "SessionId",
    "SourceDocument",
    "StreamError",
]
