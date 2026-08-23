"""EmbeddedOps HTTP API: unified task entrypoint and capability listing.

``POST /embedded/tasks`` is the product entrypoint: submit a goal, get a
task id, and drive the graph through its approval interrupt. The per-step
endpoints (design/firmware/debug/simulate) remain available internally for
debugging; everything user-facing flows through tasks.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, field_validator

from network_agent_rag.agents.embedded.workflow import create_embedded_workflow
from network_agent_rag.artifact import ArtifactStore, FileSystemArtifactStore
from network_agent_rag.auth import Permission
from network_agent_rag.auth.dependencies import require_permission
from network_agent_rag.capability import CapabilityRegistry, CapabilityType
from network_agent_rag.capability.defaults import register_default_capabilities
from network_agent_rag.core.config import Settings
from network_agent_rag.domain.task import InMemoryTaskStore, Task, TaskDomain, TaskStatus
from network_agent_rag.domain.task.models import new_task_id
from network_agent_rag.infrastructure.simulation import InProcessSimulatorBackend


_require_embedded_generate = require_permission(Permission.EMBEDDED_GENERATE)
_require_embedded_read = require_permission(Permission.EMBEDDED_READ)
_require_embedded_simulate = require_permission(Permission.EMBEDDED_SIMULATE)


class EmbeddedServices:
    """Wired dependencies for the embedded API (single-process default)."""

    def __init__(
        self,
        *,
        task_store: InMemoryTaskStore | None = None,
        capability_registry: CapabilityRegistry | None = None,
        artifact_store: ArtifactStore | None = None,
        artifact_root: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.task_store = task_store or InMemoryTaskStore()
        self.capability_registry = capability_registry or register_default_capabilities(
            CapabilityRegistry()
        )
        self.artifact_store = artifact_store or FileSystemArtifactStore(
            artifact_root or self.settings.artifact_root
        )
        self.backend = InProcessSimulatorBackend()
        self.checkpointer = InMemorySaver()
        self._sequence = 0

    def next_task_id(self) -> str:
        self._sequence += 1
        return new_task_id(TaskDomain.EMBEDDED, self._sequence)

    def graph(self) -> Any:
        return create_embedded_workflow(
            backend=self.backend,
            capability_registry=self.capability_registry,
            artifact_store=self.artifact_store,
            checkpointer=self.checkpointer,
        )


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value.strip()


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str
    status: TaskStatus
    validation_state: str | None
    summary: str | None
    error: str | None
    final_report: str | None = None


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    actor: str = Field(min_length=1)
    comment: str = ""


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    type: str
    name: str
    version: str
    created_by: str
    sha256: str
    size_bytes: int


class TaskDetailResponse(TaskResponse):
    artifacts: list[ArtifactResponse] = Field(default_factory=list)
    approval_request: dict[str, Any] | None = None
    firmware_filename: str | None = None


class CapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    type: str
    permission: str
    backend: str
    enabled: bool
    metadata: dict[str, Any]


def create_embedded_router(
    services: EmbeddedServices | None = None,
) -> APIRouter:
    services = services or EmbeddedServices()
    router = APIRouter(prefix="/embedded", tags=["embedded"])
    graph = services.graph()

    def _sync_task_status(task_id: str) -> Task:
        task = services.task_store.get(task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
        snapshot = graph.get_state({"configurable": {"thread_id": task_id}})
        if snapshot is None or not snapshot.values:
            return task
        if snapshot.next:
            return services.task_store.update_status(
                task_id, TaskStatus.AWAITING_APPROVAL
            )
        validation_state = snapshot.values.get("validation_state")
        if validation_state == "PASSED":
            return services.task_store.update_status(
                task_id,
                TaskStatus.COMPLETED,
                validation_state=validation_state,
                summary=snapshot.values.get("final_report", ""),
            )
        if validation_state == "FAILED":
            return services.task_store.update_status(
                task_id,
                TaskStatus.FAILED,
                validation_state=validation_state,
                summary=snapshot.values.get("final_report", ""),
                error=snapshot.values.get("error"),
            )
        return services.task_store.update_status(
            task_id, TaskStatus.RUNNING, validation_state=validation_state
        )

    def _latest_state(task_id: str) -> dict[str, Any]:
        snapshot = graph.get_state({"configurable": {"thread_id": task_id}})
        return dict(snapshot.values) if snapshot is not None and snapshot.values else {}

    def _pending_approval(task_id: str) -> dict[str, Any] | None:
        snapshot = graph.get_state({"configurable": {"thread_id": task_id}})
        if snapshot is None or not snapshot.next:
            return None
        for task in snapshot.tasks or ():
            for interrupt_value in getattr(task, "interrupts", None) or ():
                payload = getattr(interrupt_value, "value", None)
                if isinstance(payload, dict) and "approval_request" in payload:
                    return payload["approval_request"]
        return None

    def _task_response(task: Task, final_report: str | None = None) -> TaskResponse:
        return TaskResponse(
            task_id=task.task_id,
            goal=task.goal,
            status=task.status,
            validation_state=task.validation_state,
            summary=task.summary,
            error=task.error,
            final_report=final_report,
        )

    @router.post(
        "/tasks",
        response_model=TaskResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_task(
        payload: TaskCreateRequest,
        _: Annotated[None, Depends(_require_embedded_generate)],
    ) -> TaskResponse:
        task_id = services.next_task_id()
        services.task_store.create(
            Task(
                task_id=task_id,
                goal=payload.goal,
                domain=TaskDomain.EMBEDDED,
                status=TaskStatus.RUNNING,
                workflow_id=task_id,
            )
        )
        config = {"configurable": {"thread_id": task_id}}
        graph.invoke({"task_id": task_id, "goal": payload.goal}, config)
        task = _sync_task_status(task_id)
        return _task_response(
            task, final_report=_latest_state(task_id).get("final_report", "")
        )

    @router.get("/tasks", response_model=list[TaskResponse])
    def list_tasks(
        _: Annotated[None, Depends(_require_embedded_read)],
    ) -> list[TaskResponse]:
        return [
            _task_response(task)
            for task in services.task_store.list(TaskDomain.EMBEDDED)
        ]

    @router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
    def get_task(
        task_id: str,
        _: Annotated[None, Depends(_require_embedded_read)],
    ) -> TaskDetailResponse:
        task = services.task_store.get(task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
        current = _latest_state(task_id)
        firmware = current.get("firmware") or {}
        return TaskDetailResponse(
            **_task_response(task, final_report=current.get("final_report", "")).model_dump(),
            approval_request=(
                current.get("approval_request") or _pending_approval(task_id)
            ),
            firmware_filename=firmware.get("filename"),
            artifacts=[
                ArtifactResponse(
                    artifact_id=artifact.artifact_id,
                    type=artifact.type.value,
                    name=artifact.name,
                    version=artifact.version,
                    created_by=artifact.created_by,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                )
                for artifact in services.artifact_store.list(task_id)
            ],
        )

    @router.post("/tasks/{task_id}/approval", response_model=TaskResponse)
    def decide_approval(
        task_id: str,
        payload: ApprovalDecisionRequest,
        _: Annotated[None, Depends(_require_embedded_simulate)],
    ) -> TaskResponse:
        task = services.task_store.get(task_id)
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
        snapshot = graph.get_state({"configurable": {"thread_id": task_id}})
        if snapshot is None or not snapshot.next:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "task is not awaiting approval"
            )
        config = {"configurable": {"thread_id": task_id}}
        graph.invoke(
            Command(
                resume={
                    "decision": payload.decision,
                    "actor": payload.actor,
                    "comment": payload.comment,
                }
            ),
            config,
        )
        task = _sync_task_status(task_id)
        return _task_response(
            task, final_report=_latest_state(task_id).get("final_report", "")
        )

    return router


def create_capabilities_router(
    services: EmbeddedServices | None = None,
) -> APIRouter:
    services = services or EmbeddedServices()
    router = APIRouter(prefix="/capabilities", tags=["capabilities"])

    @router.get("", response_model=list[CapabilityResponse])
    def list_capabilities(
        _: Annotated[None, Depends(_require_embedded_read)],
        type: CapabilityType | None = None,
        permission: Permission | None = None,
    ) -> list[CapabilityResponse]:
        capabilities = services.capability_registry.list(
            type=type, permission=permission, enabled=True
        )
        return [
            CapabilityResponse(
                name=capability.name,
                version=capability.version,
                type=capability.type.value,
                permission=capability.permission.value,
                backend=capability.backend,
                enabled=capability.enabled,
                metadata=capability.metadata,
            )
            for capability in capabilities
        ]

    return router


__all__ = [
    "EmbeddedServices",
    "create_capabilities_router",
    "create_embedded_router",
]
