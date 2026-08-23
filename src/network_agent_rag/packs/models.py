"""DomainPack contract: declarative assets every domain must register.

The contract deliberately includes governance assets (permissions + roles)
as mandatory fields -- without them, domain extraction decouples code but
fails at the governance layer (charter iron law 5).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from network_agent_rag.capability.models import Capability


_PACK_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_IMPORT_PATH_PATTERN = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class PackModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LifecycleStatus(StrEnum):
    """Placeholder lifecycle state; full semantics arrive in v0.20."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class LifecycleSpec(PackModel):
    status: LifecycleStatus = LifecycleStatus.ENABLED


def _validate_import_path(value: str) -> str:
    if not _IMPORT_PATH_PATTERN.match(value):
        raise ValueError(f"import path must be 'module:attr', got: {value!r}")
    return value


class RouterSpec(PackModel):
    prefix: str = Field(min_length=1)
    factory: str = Field(min_length=1)

    @field_validator("factory")
    @classmethod
    def check_factory(cls, value: str) -> str:
        return _validate_import_path(value)


class UiExtensionSpec(PackModel):
    """Console navigation entry; schema-accepted, runtime-ignored in v0.16."""

    view_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    glyph: str = Field(min_length=1)


class PermissionSpec(PackModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class RoleSpec(PackModel):
    name: str = Field(min_length=1, max_length=128)
    permissions: tuple[str, ...] = Field(min_length=1)
    description: str = ""

    @field_validator("permissions")
    @classmethod
    def reject_duplicates(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("role permissions must not contain duplicates")
        return values


class KnowledgeCollectionSpec(PackModel):
    name: str = Field(min_length=1)
    directory: str = Field(min_length=1)


class DomainPackSpec(PackModel):
    """A versioned bundle of domain assets registered into the platform."""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    description: str = ""
    lifecycle: LifecycleSpec = Field(default_factory=LifecycleSpec)
    capabilities: tuple[Capability, ...] = ()
    agent_factories: tuple[str, ...] = ()
    workflow_factories: tuple[str, ...] = ()
    permissions: tuple[PermissionSpec, ...] = Field(min_length=1)
    roles: tuple[RoleSpec, ...] = Field(min_length=1)
    knowledge_collections: tuple[KnowledgeCollectionSpec, ...] = ()
    evaluation_cases: tuple[str, ...] = ()
    api_routers: tuple[RouterSpec, ...] = ()
    ui_extensions: tuple[UiExtensionSpec, ...] = ()
    depends_on: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not _PACK_NAME_PATTERN.match(value):
            raise ValueError("pack name must match ^[a-z][a-z0-9_]*$")
        return value

    @field_validator("version")
    @classmethod
    def check_version(cls, value: str) -> str:
        if not _VERSION_PATTERN.match(value):
            raise ValueError("pack version must be MAJOR.MINOR.PATCH")
        return value

    @field_validator("agent_factories", "workflow_factories")
    @classmethod
    def check_factories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_import_path(value) for value in values)

    @field_validator("permissions")
    @classmethod
    def reject_duplicate_permissions(
        cls, values: tuple[PermissionSpec, ...]
    ) -> tuple[PermissionSpec, ...]:
        names = [item.name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("pack permissions must not contain duplicates")
        return values

    @field_validator("roles")
    @classmethod
    def reject_duplicate_roles(cls, values: tuple[RoleSpec, ...]) -> tuple[RoleSpec, ...]:
        names = [item.name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("pack roles must not contain duplicates")
        return values

    @model_validator(mode="after")
    def reject_self_dependency(self) -> "DomainPackSpec":
        if self.name in self.depends_on:
            raise ValueError("pack must not depend on itself")
        return self

    @property
    def permission_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.permissions)


__all__ = [
    "DomainPackSpec",
    "KnowledgeCollectionSpec",
    "LifecycleSpec",
    "LifecycleStatus",
    "PermissionSpec",
    "RoleSpec",
    "RouterSpec",
    "UiExtensionSpec",
]
