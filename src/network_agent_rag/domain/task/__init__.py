"""Unified cross-domain task model shared by NetworkOps and EmbeddedOps."""

from network_agent_rag.domain.task.models import (
    Task,
    TaskDomain,
    TaskStatus,
    new_task_id,
)
from network_agent_rag.domain.task.store import InMemoryTaskStore, TaskStore

__all__ = [
    "InMemoryTaskStore",
    "Task",
    "TaskDomain",
    "TaskStatus",
    "TaskStore",
    "new_task_id",
]
