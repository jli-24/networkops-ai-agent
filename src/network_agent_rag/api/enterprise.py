"""Isolated FastAPI service for checkpointed enterprise incidents."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import os

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from network_agent_rag.agents.enterprise import ApprovalConflict, validate_approval
from network_agent_rag.api.enterprise_schemas import (
    ApprovalDecisionRequest,
    AuditResponse,
    IncidentId,
    IncidentRequest,
    IncidentStatusResponse,
)
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.core.config import Settings
from network_agent_rag.main import create_app


enterprise_router = APIRouter()


def create_enterprise_app(
    *,
    agent_workflow: Any = None,
    audit_log: SQLiteAuditLog | None = None,
    clock: Callable[[], datetime] | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Create an app with opt-in enterprise incident routes."""

    application = create_app(lifespan=lifespan)
    application.state.enterprise_workflow = agent_workflow
    application.state.audit_log = audit_log
    application.state.enterprise_clock = clock or (lambda: datetime.now(timezone.utc))
    application.include_router(enterprise_router, prefix=Settings().api_prefix)
    return application


def create_sqlite_enterprise_app(
    *,
    workflow_factory: Callable[[Any, SQLiteAuditLog], Any],
    checkpoint_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Create an app whose lifespan owns the async SQLite checkpointer."""

    settings = Settings()
    os.environ.setdefault(
        "LANGGRAPH_STRICT_MSGPACK",
        str(settings.langgraph_strict_msgpack).lower(),
    )
    checkpoint = Path(checkpoint_path or settings.checkpoint_db_path)
    audit = Path(audit_path or settings.audit_db_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        audit_log = SQLiteAuditLog(audit)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            await saver.setup()
            application.state.enterprise_workflow = workflow_factory(saver, audit_log)
            application.state.audit_log = audit_log
            yield

    return create_enterprise_app(clock=clock, lifespan=lifespan)


@enterprise_router.post("/incidents", response_class=StreamingResponse)
async def start_incident(payload: IncidentRequest, request: Request) -> StreamingResponse:
    workflow = _workflow(request)
    incident_id = payload.incident_id or uuid4().hex
    config = _config(incident_id)
    snapshot = await workflow.aget_state(config)
    if snapshot.values:
        raise HTTPException(status_code=409, detail="Incident ID already exists")
    graph_input = {
        "user_query": payload.query,
        "incident_id": incident_id,
        "session_id": payload.session_id,
    }
    return _stream_response(
        _stream_workflow(workflow, graph_input, config, incident_id)
    )


@enterprise_router.get(
    "/incidents/{incident_id}", response_model=IncidentStatusResponse
)
async def incident_status(incident_id: IncidentId, request: Request) -> IncidentStatusResponse:
    snapshot = await _snapshot(request, incident_id)
    return _status_response(incident_id, dict(snapshot.values))


@enterprise_router.post(
    "/incidents/{incident_id}/approval", response_class=StreamingResponse
)
async def decide_approval(
    incident_id: IncidentId,
    payload: ApprovalDecisionRequest,
    request: Request,
) -> StreamingResponse:
    workflow = _workflow(request)
    snapshot = await _snapshot(request, incident_id)
    values = dict(snapshot.values)
    if values.get("enterprise_status") != "pending_approval":
        raise HTTPException(status_code=409, detail="Incident is not pending approval")
    try:
        validate_approval(
            values,
            payload.model_dump(),
            now=request.app.state.enterprise_clock(),
        )
    except ApprovalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    config = _config(incident_id)
    command = Command(resume=payload.model_dump())
    return _stream_response(
        _stream_workflow(workflow, command, config, incident_id)
    )


@enterprise_router.get(
    "/incidents/{incident_id}/audit", response_model=AuditResponse
)
async def incident_audit(incident_id: IncidentId, request: Request) -> AuditResponse:
    await _snapshot(request, incident_id)
    audit_log = request.app.state.audit_log
    if audit_log is None:
        raise HTTPException(status_code=503, detail="Audit log is not configured")
    return AuditResponse(
        incident_id=incident_id,
        events=audit_log.list_events(incident_id),
    )


async def _snapshot(request: Request, incident_id: str) -> Any:
    snapshot = await _workflow(request).aget_state(_config(incident_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Incident not found")
    return snapshot


def _workflow(request: Request) -> Any:
    workflow = getattr(request.app.state, "enterprise_workflow", None)
    if workflow is None:
        raise HTTPException(status_code=503, detail="Enterprise workflow is not configured")
    return workflow


async def _stream_workflow(
    workflow: Any,
    graph_input: dict[str, object] | Command,
    config: dict[str, dict[str, str]],
    incident_id: str,
) -> AsyncIterator[str]:
    yield _sse("start", {"incident_id": incident_id})
    try:
        async for chunk in workflow.astream(
            graph_input,
            config,
            stream_mode="updates",
        ):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    payload = _interrupt_value(update)
                    yield _sse("approval_required", payload)
                elif not node.startswith("__"):
                    yield _sse("node", {"incident_id": incident_id, "node": node})
        snapshot = await workflow.aget_state(config)
        if any(task.interrupts for task in snapshot.tasks):
            return
        response = _status_response(incident_id, dict(snapshot.values))
        yield _sse("answer", response.model_dump(mode="json"))
    except Exception:
        yield _sse(
            "error",
            {
                "incident_id": incident_id,
                "code": "ENTERPRISE_WORKFLOW_FAILED",
                "message": "Enterprise workflow execution failed.",
            },
        )


def _interrupt_value(value: object) -> dict[str, object]:
    items = value if isinstance(value, (tuple, list)) else (value,)
    for item in items:
        payload = getattr(item, "value", item)
        if isinstance(payload, dict):
            return payload
    raise TypeError("Approval interrupt payload must be a dictionary")


def _status_response(
    incident_id: str,
    values: dict[str, object],
) -> IncidentStatusResponse:
    return IncidentStatusResponse(
        incident_id=incident_id,
        enterprise_status=str(values.get("enterprise_status", "unknown")),
        risk_decision=values.get("risk_decision"),
        approval_result=values.get("approval_result"),
        execution_result=values.get("execution_result"),
        answer=str(values.get("answer", "")),
        error=values.get("error"),
    )


def _config(incident_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": incident_id}}


def _stream_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(
        jsonable_encoder(data), ensure_ascii=False, separators=(",", ":")
    )
    return f"event: {event}\ndata: {payload}\n\n"


__all__ = [
    "create_enterprise_app",
    "create_sqlite_enterprise_app",
    "enterprise_router",
]
