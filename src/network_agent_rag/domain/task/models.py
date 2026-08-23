"""Unified task contracts: one runtime model for every agent domain."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskDomain(StrEnum):
    NETWORK = "network"
    EMBEDDED = "embedded"


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    domain: TaskDomain
    status: TaskStatus = TaskStatus.CREATED
    workflow_id: str | None = None
    validation_state: str | None = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("goal")
    @classmethod
    def reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


def new_task_id(domain: TaskDomain, sequence: int) -> str:
    """Render a stable, human-readable task id like ``emb-000042``."""

    prefix = "emb" if domain == TaskDomain.EMBEDDED else "net"
    return f"{prefix}-{sequence:06d}"


__all__ = ["Task", "TaskDomain", "TaskStatus", "new_task_id"]
