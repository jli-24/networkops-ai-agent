"""Strict identity and authorization models for the RBAC foundation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class Role(StrEnum):
    OPERATOR = "Operator"
    ENGINEER = "Engineer"
    EMBEDDED_ENGINEER = "EmbeddedEngineer"
    ADMIN = "Admin"


class Permission(StrEnum):
    VIEW_INCIDENT = "VIEW_INCIDENT"
    VIEW_TRACE = "VIEW_TRACE"
    CREATE_REPAIR_PLAN = "CREATE_REPAIR_PLAN"
    EXECUTE_REPAIR = "EXECUTE_REPAIR"
    APPROVE_REPAIR = "APPROVE_REPAIR"
    MANAGE_SYSTEM = "MANAGE_SYSTEM"
    EMBEDDED_READ = "EMBEDDED_READ"
    EMBEDDED_GENERATE = "EMBEDDED_GENERATE"
    EMBEDDED_SIMULATE = "EMBEDDED_SIMULATE"


class User(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: NonEmptyString
    username: NonEmptyString
    roles: tuple[Role, ...] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def reject_duplicate_roles(cls, roles: tuple[Role, ...]) -> tuple[Role, ...]:
        if len(roles) != len(set(roles)):
            raise ValueError("roles must not contain duplicates")
        return roles


__all__ = ["Permission", "Role", "User"]
