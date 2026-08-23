"""Task store contract and thread-safe in-memory implementation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from network_agent_rag.domain.task.models import Task, TaskDomain, TaskStatus


class TaskStore(Protocol):
    """Persistence boundary for unified tasks."""

    def create(self, task: Task) -> Task: ...

    def get(self, task_id: str) -> Task | None: ...

    def list(self, domain: TaskDomain | None = None) -> list[Task]: ...

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        workflow_id: str | None = None,
        validation_state: str | None = None,
        summary: str | None = None,
        error: str | None = None,
    ) -> Task: ...


class InMemoryTaskStore:
    """Deterministic task store for tests, demos, and single-process runs."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, task: Task) -> Task:
        now = self._clock()
        stored = task.model_copy(
            update={"created_at": task.created_at or now, "updated_at": now}
        )
        self._tasks[stored.task_id] = stored
        return stored.model_copy()

    def get(self, task_id: str) -> Task | None:
        task = self._tasks.get(task_id)
        return task.model_copy() if task is not None else None

    def list(self, domain: TaskDomain | None = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if domain is not None:
            tasks = [task for task in tasks if task.domain == domain]
        return sorted(tasks, key=lambda item: item.created_at or item.now())

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        workflow_id: str | None = None,
        validation_state: str | None = None,
        summary: str | None = None,
        error: str | None = None,
    ) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        updates: dict[str, object] = {"status": status, "updated_at": self._clock()}
        if workflow_id is not None:
            updates["workflow_id"] = workflow_id
        if validation_state is not None:
            updates["validation_state"] = validation_state
        if summary is not None:
            updates["summary"] = summary
        if error is not None:
            updates["error"] = error
        updated = task.model_copy(update=updates)
        self._tasks[task_id] = updated
        return updated.model_copy()


__all__ = ["InMemoryTaskStore", "TaskStore"]
