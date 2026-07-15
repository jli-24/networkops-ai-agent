"""Application health, streaming chat, and history routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import json
import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.documents import Document
from pydantic import BaseModel

from network_agent_rag.api.history import InMemoryHistoryStore
from network_agent_rag.api.schemas import (
    ChatRequest,
    ChatResponse,
    HistoryResponse,
    NodeProgress,
    SessionId,
    SourceDocument,
    StreamError,
)


api_router = APIRouter()
logger = logging.getLogger(__name__)


@api_router.get("/health")
async def health() -> dict[str, str]:
    """Report that the service is running."""

    return {"status": "ok"}


@api_router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {"schema": {"type": "string"}},
            }
        }
    },
)
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Stream LangGraph node progress and the final answer as SSE."""

    workflow = request.app.state.agent_workflow
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent workflow is not configured",
        )
    history_store = request.app.state.history_store
    return StreamingResponse(
        _stream_chat(workflow, history_store, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_router.get("/history", response_model=HistoryResponse)
async def history(
    request: Request,
    session_id: SessionId,
) -> HistoryResponse:
    """Return process-local messages for one session."""

    history_store = request.app.state.history_store
    return HistoryResponse(
        session_id=session_id,
        messages=history_store.get(session_id),
    )


async def _stream_chat(
    workflow: Any,
    history_store: InMemoryHistoryStore,
    payload: ChatRequest,
) -> AsyncIterator[str]:
    yield _sse("start", {"session_id": payload.session_id})
    state: dict[str, Any] = {"user_query": payload.query}
    try:
        async for chunk in workflow.astream(
            {"user_query": payload.query},
            stream_mode="updates",
        ):
            if not isinstance(chunk, dict):
                raise TypeError("Agent workflow updates must be dictionaries")
            for node, update in chunk.items():
                if isinstance(update, dict):
                    state.update(update)
                if not node.startswith("__"):
                    progress = NodeProgress(
                        session_id=payload.session_id,
                        node=node,
                        iteration=state.get("iteration"),
                    )
                    yield _sse("node", progress)

        response = _chat_response(payload.session_id, state)
        answer_event = _sse("answer", response)
        yield answer_event
        history_store.append_exchange(
            payload.session_id,
            payload.query,
            response.answer,
        )
    except Exception:
        logger.exception("Agent workflow execution failed")
        error = StreamError(
            session_id=payload.session_id,
            code="AGENT_EXECUTION_FAILED",
            message="Agent execution failed.",
        )
        yield _sse("error", error)


def _chat_response(session_id: str, state: dict[str, Any]) -> ChatResponse:
    documents = state.get("documents", [])
    if not isinstance(documents, list) or not all(
        isinstance(document, Document) for document in documents
    ):
        raise TypeError("Agent state documents must be list[Document]")
    return ChatResponse(
        session_id=session_id,
        answer=state["answer"],
        intent=state["intent"],
        rewritten_query=state["rewritten_query"],
        relevance_score=state["relevance_score"],
        source_documents=[
            SourceDocument(
                page_content=document.page_content,
                metadata=dict(document.metadata),
            )
            for document in documents
        ],
        topology_context=state["topology_context"],
        metrics=state["metrics"],
        iteration=state["iteration"],
        error=state["error"],
    )


def _sse(event: str, data: BaseModel | dict[str, Any]) -> str:
    if isinstance(data, BaseModel):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"
