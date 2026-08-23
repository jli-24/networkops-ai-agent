"""Isolated FastAPI service for checkpointed enterprise incidents."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    ExitStack,
    asynccontextmanager,
    contextmanager,
)
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import uuid4
import asyncio
import hashlib
import json
import os

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from network_agent_rag.packs.networkops.agents.enterprise import ApprovalConflict, validate_approval
from network_agent_rag.packs.networkops.api.enterprise_schemas import (
    ApprovalDecisionRequest,
    AuditResponse,
    EnterpriseTraceResponse,
    IncidentId,
    IncidentRequest,
    IncidentStatusResponse,
)
from network_agent_rag.api.benchmarks import benchmark_router
from network_agent_rag.packs.networkops.api.console import (
    EvaluationReportProvider,
    console_router,
)
from network_agent_rag.api.observability import (
    enterprise_metrics_router,
    metrics_router,
    observability_router,
)
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.auth import (
    AuthenticationError,
    AuthenticationProvider,
    AuthorizationError,
    JWTProvider,
    JWTTokenManager,
    Permission,
    UserContext,
    open_redis_identity_authentication,
)
from network_agent_rag.auth.dependencies import (
    current_user_dependency,
    require_permission as require_api_permission,
)
from network_agent_rag.core.config import Settings
from network_agent_rag.main import create_app
from network_agent_rag.observability import (
    SQLiteTraceStore,
    MetricsStore,
    SQLiteMetricsStore,
    SpanKind,
    SpanStatus,
    TraceCollector,
    TracePersistenceError,
    load_trace_events,
)
from network_agent_rag.evaluation import BenchmarkResultStore
from network_agent_rag.policy import PolicyEngine, default_policy_registry
from network_agent_rag.storage.base import AuditStore, TraceStore
from network_agent_rag.storage.postgres import (
    open_postgres_checkpointer,
    open_postgres_stores,
)
from network_agent_rag.storage.redis import open_redis_checkpointer
from network_agent_rag.storage.sqlite import (
    create_sqlite_stores,
    open_sqlite_checkpointer,
)


enterprise_router = APIRouter()
_require_create_repair_plan = require_api_permission(
    Permission.CREATE_REPAIR_PLAN
)
_require_approve_repair = require_api_permission(Permission.APPROVE_REPAIR)
_require_execute_repair = require_api_permission(Permission.EXECUTE_REPAIR)


class LegacyWorkflowFactory(Protocol):
    def __call__(self, checkpointer: Any, audit_log: AuditStore) -> Any: ...


class ObservedWorkflowFactory(Protocol):
    def __call__(
        self,
        checkpointer: Any,
        *,
        audit_log: AuditStore | None = None,
        trace_store: TraceStore | None = None,
        metrics_store: MetricsStore | None = None,
        trace_collector: TraceCollector | None = None,
    ) -> Any: ...


class PolicyWorkflowFactory(Protocol):
    def __call__(
        self,
        checkpointer: Any,
        *,
        policy_engine: PolicyEngine,
        audit_log: AuditStore | None = None,
        trace_store: TraceStore | None = None,
        metrics_store: MetricsStore | None = None,
        trace_collector: TraceCollector | None = None,
    ) -> Any: ...


def create_enterprise_app(
    *,
    agent_workflow: Any = None,
    audit_log: AuditStore | None = None,
    trace_store: TraceStore | None = None,
    trace_collector: TraceCollector | None = None,
    metrics_store: MetricsStore | None = None,
    benchmark_store: BenchmarkResultStore | None = None,
    clock: Callable[[], datetime] | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    authentication_provider: AuthenticationProvider | None = None,
    user_context_provider: Callable[[Request], UserContext | None] | None = None,
    evaluation_report_provider: EvaluationReportProvider | None = None,
) -> FastAPI:
    """Create an app with opt-in enterprise incident routes."""

    if authentication_provider is not None and user_context_provider is not None:
        raise ValueError(
            "authentication_provider and user_context_provider are mutually exclusive"
        )
    application = create_app(lifespan=lifespan)
    application.state.enterprise_workflow = agent_workflow
    application.state.audit_log = audit_log
    application.state.trace_store = trace_store
    application.state.trace_collector = trace_collector
    application.state.metrics_store = (
        metrics_store
        if metrics_store is not None
        else SQLiteMetricsStore(trace_store, audit_log)
        if trace_store is not None
        else None
    )
    application.state.benchmark_store = benchmark_store
    application.state.evaluation_report_provider = evaluation_report_provider
    application.state.enterprise_clock = clock or (lambda: datetime.now(timezone.utc))
    application.state.authentication_provider = authentication_provider
    application.state.user_context_provider = user_context_provider
    application.add_exception_handler(
        AuthenticationError,
        _authentication_error_response,
    )
    application.add_exception_handler(
        AuthorizationError,
        _authorization_error_response,
    )
    application.include_router(enterprise_router, prefix=Settings().api_prefix)
    application.include_router(observability_router, prefix=Settings().api_prefix)
    application.include_router(enterprise_metrics_router, prefix=Settings().api_prefix)
    application.include_router(benchmark_router, prefix=Settings().api_prefix)
    application.include_router(console_router, prefix=Settings().api_prefix)
    application.include_router(metrics_router)
    return application


def create_sqlite_enterprise_app(
    *,
    workflow_factory: LegacyWorkflowFactory | None = None,
    observed_workflow_factory: ObservedWorkflowFactory | None = None,
    policy_workflow_factory: PolicyWorkflowFactory | None = None,
    checkpoint_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    observability_path: str | Path | None = None,
    benchmark_results_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    authentication_provider: AuthenticationProvider | None = None,
    user_context_provider: Callable[[Request], UserContext | None] | None = None,
    evaluation_report_provider: EvaluationReportProvider | None = None,
) -> FastAPI:
    """Create an app whose lifespan owns the async SQLite checkpointer."""

    if sum(
        factory is not None
        for factory in (
            workflow_factory,
            observed_workflow_factory,
            policy_workflow_factory,
        )
    ) != 1:
        raise ValueError(
            "configure exactly one workflow factory"
        )

    settings = Settings()
    resolved_authentication = _resolve_authentication_provider(
        settings,
        authentication_provider,
        user_context_provider,
    )
    os.environ.setdefault(
        "LANGGRAPH_STRICT_MSGPACK",
        str(settings.langgraph_strict_msgpack).lower(),
    )
    checkpoint = Path(checkpoint_path or settings.checkpoint_db_path)
    audit = Path(audit_path or settings.audit_db_path)
    observability = Path(observability_path or settings.observability_db_path)
    benchmark_results = Path(
        benchmark_results_path or settings.benchmark_results_path
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        audit_log = SQLiteAuditLog(audit)
        trace_store = SQLiteTraceStore(observability)
        trace_collector = TraceCollector(audit_log)
        metrics_store = SQLiteMetricsStore(trace_store, audit_log)
        benchmark_store = BenchmarkResultStore(benchmark_results)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            await saver.setup()
            if policy_workflow_factory is not None:
                workflow = policy_workflow_factory(
                    saver,
                    policy_engine=PolicyEngine(default_policy_registry()),
                    audit_log=audit_log,
                    trace_store=trace_store,
                    metrics_store=metrics_store,
                    trace_collector=trace_collector,
                )
            elif observed_workflow_factory is not None:
                workflow = observed_workflow_factory(
                    saver,
                    audit_log=audit_log,
                    trace_store=trace_store,
                    metrics_store=metrics_store,
                    trace_collector=trace_collector,
                )
            else:
                assert workflow_factory is not None
                workflow = workflow_factory(saver, audit_log)
            application.state.enterprise_workflow = workflow
            application.state.audit_log = audit_log
            application.state.trace_store = trace_store
            application.state.trace_collector = trace_collector
            application.state.metrics_store = metrics_store
            application.state.benchmark_store = benchmark_store
            yield

    return create_enterprise_app(
        clock=clock,
        lifespan=lifespan,
        authentication_provider=resolved_authentication,
        user_context_provider=user_context_provider,
        evaluation_report_provider=evaluation_report_provider,
    )


def create_storage_enterprise_app(
    *,
    workflow_factory: LegacyWorkflowFactory | None = None,
    observed_workflow_factory: ObservedWorkflowFactory | None = None,
    policy_workflow_factory: PolicyWorkflowFactory | None = None,
    storage_backend: str | None = None,
    checkpoint_backend: str | None = None,
    database_url: str | None = None,
    redis_url: str | None = None,
    identity_redis_url: str | None = None,
    identity_token_manager: JWTTokenManager | None = None,
    checkpoint_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    observability_path: str | Path | None = None,
    benchmark_results_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    authentication_provider: AuthenticationProvider | None = None,
    user_context_provider: Callable[[Request], UserContext | None] | None = None,
    evaluation_report_provider: EvaluationReportProvider | None = None,
) -> FastAPI:
    """Create an enterprise app with independently selected storage domains."""

    if sum(
        factory is not None
        for factory in (
            workflow_factory,
            observed_workflow_factory,
            policy_workflow_factory,
        )
    ) != 1:
        raise ValueError(
            "configure exactly one workflow factory"
        )
    settings = Settings()
    identity_redis = (
        identity_redis_url
        if identity_redis_url is not None
        else settings.identity_redis_url
    )
    if identity_redis and (
        authentication_provider is not None or user_context_provider is not None
    ):
        raise ValueError(
            "identity authentication cannot be combined with an explicit provider"
        )
    if (
        identity_redis
        and identity_token_manager is None
        and settings.jwt_secret_key is None
    ):
        raise ValueError("JWT_SECRET_KEY is required for identity authentication")
    resolved_authentication = (
        None
        if identity_redis
        else _resolve_authentication_provider(
            settings,
            authentication_provider,
            user_context_provider,
        )
    )
    storage_name = storage_backend or settings.storage_backend
    checkpoint_name = checkpoint_backend or settings.checkpoint_backend
    if storage_name not in {"sqlite", "postgres"}:
        raise ValueError("storage backend must be sqlite or postgres")
    if checkpoint_name not in {"sqlite", "postgres", "redis"}:
        raise ValueError("checkpoint backend must be sqlite, postgres, or redis")
    database = database_url if database_url is not None else settings.database_url
    redis = redis_url if redis_url is not None else settings.redis_url
    if (storage_name == "postgres" or checkpoint_name == "postgres") and not database:
        raise ValueError("DATABASE_URL is required for PostgreSQL storage")
    if checkpoint_name == "redis" and not redis:
        raise ValueError("REDIS_URL is required for Redis checkpoint storage")

    os.environ.setdefault(
        "LANGGRAPH_STRICT_MSGPACK",
        str(settings.langgraph_strict_msgpack).lower(),
    )
    checkpoint = Path(checkpoint_path or settings.checkpoint_db_path)
    audit = Path(audit_path or settings.audit_db_path)
    observability = Path(observability_path or settings.observability_db_path)
    benchmark_results = Path(
        benchmark_results_path or settings.benchmark_results_path
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        with _storage_context(
            storage_name,
            audit_path=audit,
            trace_path=observability,
            database_url=database,
        ) as (audit_log, trace_store):
            with _identity_authentication_context(
                identity_redis,
                settings=settings,
                audit_log=audit_log,
                fallback=resolved_authentication,
                token_manager=identity_token_manager,
            ) as active_authentication:
                trace_collector = TraceCollector(audit_log)
                metrics_store = SQLiteMetricsStore(trace_store, audit_log)
                benchmark_store = BenchmarkResultStore(benchmark_results)
                async with _checkpoint_context(
                    checkpoint_name,
                    sqlite_path=checkpoint,
                    database_url=database,
                    redis_url=redis,
                ) as saver:
                    if policy_workflow_factory is not None:
                        workflow = policy_workflow_factory(
                            saver,
                            policy_engine=PolicyEngine(default_policy_registry()),
                            audit_log=audit_log,
                            trace_store=trace_store,
                            metrics_store=metrics_store,
                            trace_collector=trace_collector,
                        )
                    elif observed_workflow_factory is not None:
                        workflow = observed_workflow_factory(
                            saver,
                            audit_log=audit_log,
                            trace_store=trace_store,
                            metrics_store=metrics_store,
                            trace_collector=trace_collector,
                        )
                    else:
                        assert workflow_factory is not None
                        workflow = workflow_factory(saver, audit_log)
                    application.state.enterprise_workflow = workflow
                    application.state.audit_log = audit_log
                    application.state.trace_store = trace_store
                    application.state.trace_collector = trace_collector
                    application.state.metrics_store = metrics_store
                    application.state.benchmark_store = benchmark_store
                    application.state.authentication_provider = active_authentication
                    yield

    return create_enterprise_app(
        clock=clock,
        lifespan=lifespan,
        authentication_provider=resolved_authentication,
        user_context_provider=user_context_provider,
        evaluation_report_provider=evaluation_report_provider,
    )


@contextmanager
def _storage_context(
    backend: str,
    *,
    audit_path: Path,
    trace_path: Path,
    database_url: str | None,
) -> Iterator[tuple[AuditStore, TraceStore]]:
    if backend == "sqlite":
        yield create_sqlite_stores(audit_path, trace_path)
        return
    assert database_url is not None
    stack = ExitStack()
    try:
        stores = stack.enter_context(open_postgres_stores(database_url))
    except Exception:
        stack.close()
        raise RuntimeError("PostgreSQL storage initialization failed") from None
    with stack:
        yield stores


@asynccontextmanager
async def _checkpoint_context(
    backend: str,
    *,
    sqlite_path: Path,
    database_url: str | None,
    redis_url: str | None,
) -> AsyncIterator[Any]:
    if backend == "sqlite":
        context = open_sqlite_checkpointer(sqlite_path)
    elif backend == "postgres":
        assert database_url is not None
        context = open_postgres_checkpointer(database_url)
    else:
        assert redis_url is not None
        context = open_redis_checkpointer(redis_url)
    stack = AsyncExitStack()
    try:
        saver = await stack.enter_async_context(context)
    except Exception:
        await stack.aclose()
        raise RuntimeError(f"{backend.capitalize()} checkpoint initialization failed") from None
    async with stack:
        yield saver


@enterprise_router.post("/incidents", response_class=StreamingResponse)
async def start_incident(
    payload: IncidentRequest,
    request: Request,
    user_context: Annotated[
        UserContext | None,
        Depends(current_user_dependency),
    ],
) -> StreamingResponse:
    workflow = _workflow(request)
    incident_id = payload.incident_id or uuid4().hex
    request.state.authorization_incident_id = incident_id
    user_context = await asyncio.to_thread(
        _require_create_repair_plan,
        request,
        user_context,
    )
    config = _config(incident_id)
    snapshot = await workflow.aget_state(config)
    if snapshot.values:
        raise HTTPException(status_code=409, detail="Incident ID already exists")
    graph_input = {
        "user_query": payload.query,
        "incident_id": incident_id,
        "session_id": payload.session_id,
    }
    return _observed_stream_response(
        request,
        workflow,
        graph_input,
        incident_id,
        rbac_contexts=(
            {"repair_plan": user_context, "execution": user_context}
            if user_context is not None
            else None
        ),
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
    user_context: Annotated[
        UserContext | None,
        Depends(current_user_dependency),
    ],
) -> StreamingResponse:
    workflow = _workflow(request)
    snapshot = await _snapshot(request, incident_id)
    values = dict(snapshot.values)
    status = values.get("enterprise_status")
    if status == "approved" and values.get("execution_result") is None:
        execution_context = await asyncio.to_thread(
            _require_execute_repair,
            request,
            user_context,
        )
        _validate_execution_retry(
            values,
            payload,
            now=request.app.state.enterprise_clock(),
        )
        return _observed_stream_response(
            request,
            workflow,
            None,
            incident_id,
            resume_status="execution_retry",
            rbac_contexts=(
                {"execution": execution_context}
                if execution_context is not None
                else None
            ),
        )
    if status != "pending_approval":
        raise HTTPException(status_code=409, detail="Incident is not pending approval")
    approval_context = await asyncio.to_thread(
        _require_approve_repair,
        request,
        user_context,
    )
    try:
        validate_approval(
            values,
            payload.model_dump(),
            now=request.app.state.enterprise_clock(),
        )
    except ApprovalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    command = Command(resume=payload.model_dump())
    return _observed_stream_response(
        request,
        workflow,
        command,
        incident_id,
        rbac_contexts=(
            {
                "approval": approval_context,
                "execution": approval_context,
            }
            if approval_context is not None
            else None
        ),
    )


def _validate_execution_retry(
    values: dict[str, object],
    payload: ApprovalDecisionRequest,
    *,
    now: datetime,
) -> None:
    approval = values.get("approval_result")
    risk = values.get("risk_decision")
    if not isinstance(approval, dict) or not isinstance(risk, dict):
        raise HTTPException(status_code=409, detail="Approved execution is unavailable")
    if payload.decision != "approve":
        raise HTTPException(status_code=409, detail="Approved execution requires approve")
    if (
        payload.plan_digest != approval.get("plan_digest")
        or payload.plan_digest != risk.get("plan_digest")
        or payload.actor != approval.get("actor")
    ):
        raise HTTPException(status_code=409, detail="Approval retry does not match")
    expires_at = risk.get("expires_at")
    if not isinstance(expires_at, datetime) or now > expires_at:
        raise HTTPException(status_code=409, detail="Approval request has expired")


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
        events=await asyncio.to_thread(audit_log.list_events, incident_id),
    )


@enterprise_router.get(
    "/enterprise/incidents/{incident_id}/trace",
    response_model=EnterpriseTraceResponse,
)
def enterprise_incident_trace(
    incident_id: IncidentId,
    request: Request,
) -> EnterpriseTraceResponse:
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        raise HTTPException(status_code=503, detail="Audit log is not configured")
    events = load_trace_events(audit_log, incident_id)
    if not events:
        raise HTTPException(status_code=404, detail="Enterprise trace not found")
    return EnterpriseTraceResponse(incident_id=incident_id, events=events)


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
    graph_input: dict[str, object] | Command | None,
    config: dict[str, dict[str, object]],
    incident_id: str,
    trace_store: TraceStore | None = None,
    workflow_span_id: str | None = None,
    trace_collector: TraceCollector | None = None,
    resume_status: str | None = None,
) -> AsyncIterator[str]:
    configurable = config.get("configurable", {})
    start_payload = {"incident_id": incident_id}
    for field in ("trace_id", "run_id"):
        if field in configurable:
            start_payload[field] = configurable[field]
    yield _sse("start", start_payload)
    try:
        if trace_collector is not None and (
            isinstance(graph_input, Command) or resume_status is not None
        ):
            trace_id = configurable.get("trace_id")
            run_id = configurable.get("run_id")
            if isinstance(trace_id, str) and isinstance(run_id, str):
                resume_values = getattr(graph_input, "resume", None)
                await asyncio.to_thread(
                    trace_collector.resume,
                    incident_id=incident_id,
                    trace_id=trace_id,
                    run_id=run_id,
                    agent_name="EnterpriseWorkflow",
                    node_name="Approval",
                    input_data=(
                        {"decision": resume_values.get("decision")}
                        if isinstance(resume_values, dict)
                        else {"status": resume_status}
                    ),
                    attempt=1,
                )
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
            await asyncio.to_thread(
                _finish_workflow_span,
                trace_store,
                workflow_span_id,
                SpanStatus.INTERRUPTED,
            )
            return
        response = _status_response(incident_id, dict(snapshot.values))
        await asyncio.to_thread(
            _finish_workflow_span,
            trace_store,
            workflow_span_id,
            SpanStatus.SUCCEEDED,
        )
        yield _sse("answer", response.model_dump(mode="json"))
    except asyncio.CancelledError:
        await asyncio.to_thread(
            _finish_workflow_span,
            trace_store,
            workflow_span_id,
            SpanStatus.INTERRUPTED,
        )
        raise
    except Exception as error:
        await asyncio.to_thread(
            _finish_workflow_span,
            trace_store,
            workflow_span_id,
            SpanStatus.FAILED,
            error,
        )
        yield _sse(
            "error",
            {
                "incident_id": incident_id,
                "code": (
                    "TRACE_PERSISTENCE_FAILED"
                    if isinstance(error, TracePersistenceError)
                    else "ENTERPRISE_WORKFLOW_FAILED"
                ),
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


def _config(
    incident_id: str,
    rbac_contexts: Mapping[str, UserContext] | None = None,
) -> dict[str, dict[str, object]]:
    configurable: dict[str, object] = {"thread_id": incident_id}
    if rbac_contexts is not None:
        configurable["rbac_contexts"] = rbac_contexts
    return {"configurable": configurable}


def _observed_stream_response(
    request: Request,
    workflow: Any,
    graph_input: dict[str, object] | Command | None,
    incident_id: str,
    *,
    resume_status: str | None = None,
    rbac_contexts: Mapping[str, UserContext] | None = None,
) -> StreamingResponse:
    trace_store = getattr(request.app.state, "trace_store", None)
    trace_collector = getattr(request.app.state, "trace_collector", None)
    config = _config(incident_id, rbac_contexts)
    if isinstance(graph_input, Command) or resume_status is not None:
        config["configurable"]["is_resume"] = "true"
    async def observed_stream() -> AsyncIterator[str]:
        workflow_span_id = None
        trace_id = None
        run_id = None
        if trace_store is not None or trace_collector is not None:
            trace_id = (
                await asyncio.to_thread(
                    trace_store.trace_id_for_incident,
                    incident_id,
                )
                if trace_store is not None
                else await asyncio.to_thread(
                    _audit_trace_id,
                    trace_collector,
                    incident_id,
                )
            ) or uuid4().hex
            run_id = uuid4().hex
            config["configurable"].update(
                {
                    "trace_id": trace_id,
                    "run_id": run_id,
                }
            )
        if trace_store is not None and trace_id is not None and run_id is not None:
            span = await asyncio.to_thread(
                trace_store.start_span,
                trace_id=trace_id,
                run_id=run_id,
                incident_id=incident_id,
                kind=SpanKind.WORKFLOW,
                name="EnterpriseWorkflow",
                input_summary_hash=_workflow_digest(
                    incident_id, type(graph_input).__name__, "started"
                ),
            )
            workflow_span_id = span.span_id
            config["configurable"]["workflow_span_id"] = workflow_span_id
        async for event in _stream_workflow(
            workflow,
            graph_input,
            config,
            incident_id,
            trace_store=trace_store,
            trace_collector=trace_collector,
            workflow_span_id=workflow_span_id,
            resume_status=resume_status,
        ):
            yield event

    return _stream_response(
        observed_stream()
    )


def _audit_trace_id(
    trace_collector: TraceCollector | None,
    incident_id: str,
) -> str | None:
    if trace_collector is None:
        return None
    events = load_trace_events(trace_collector.audit_log, incident_id)
    return events[0].trace_id if events else None


def _finish_workflow_span(
    trace_store: TraceStore | None,
    span_id: str | None,
    status: SpanStatus,
    error: Exception | None = None,
) -> None:
    if trace_store is not None and span_id is not None:
        trace_store.finish_span(
            span_id,
            status=status,
            error_code=type(error).__name__ if error is not None else None,
            output_summary_hash=_workflow_digest(
                span_id, status.value, type(error).__name__ if error is not None else ""
            ),
        )


def _workflow_digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _stream_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _authorization_error_response(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Forbidden"})


async def _authentication_error_response(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_authentication_provider(
    settings: Settings,
    authentication_provider: AuthenticationProvider | None,
    user_context_provider: Callable[[Request], UserContext | None] | None,
) -> AuthenticationProvider | None:
    if authentication_provider is not None and user_context_provider is not None:
        raise ValueError(
            "authentication_provider and user_context_provider are mutually exclusive"
        )
    if authentication_provider is not None or user_context_provider is not None:
        return authentication_provider
    if settings.jwt_secret_key is None:
        return None
    return JWTProvider(
        JWTTokenManager(
            settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
            expire_minutes=settings.jwt_expire_minutes,
        )
    )


@contextmanager
def _identity_authentication_context(
    identity_redis_url: str | None,
    *,
    settings: Settings,
    audit_log: AuditStore,
    fallback: AuthenticationProvider | None,
    token_manager: JWTTokenManager | None,
) -> Iterator[AuthenticationProvider | None]:
    if not identity_redis_url:
        yield fallback
        return
    if token_manager is None:
        assert settings.jwt_secret_key is not None
        token_manager = JWTTokenManager(
            settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
            expire_minutes=settings.jwt_expire_minutes,
            access_expire_minutes=settings.jwt_access_expire_minutes,
            refresh_expire_days=settings.jwt_refresh_expire_days,
        )
    try:
        with open_redis_identity_authentication(
            identity_redis_url,
            token_manager,
            audit_log=audit_log,
        ) as provider:
            yield provider
    except Exception:
        raise RuntimeError("Identity Redis initialization failed") from None


def _sse(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(
        jsonable_encoder(data), ensure_ascii=False, separators=(",", ":")
    )
    return f"event: {event}\ndata: {payload}\n\n"


__all__ = [
    "LegacyWorkflowFactory",
    "ObservedWorkflowFactory",
    "PolicyWorkflowFactory",
    "create_enterprise_app",
    "create_sqlite_enterprise_app",
    "create_storage_enterprise_app",
    "enterprise_router",
]
