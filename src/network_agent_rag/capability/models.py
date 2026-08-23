"""Immutable contracts for the capability registry."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from network_agent_rag.auth import Permission


_VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")


class CapabilityType(StrEnum):
    FIRMWARE = "firmware"
    SIMULATION = "simulation"
    HARDWARE = "hardware"
    OPERATIONS = "operations"
    KNOWLEDGE = "knowledge"


class Capability(BaseModel):
    """A single addressable ability, versioned so variants can coexist.

    ``backend`` is a string reference identifying where the capability runs
    (``"in_process"`` today). When multiple execution providers arrive
    (local gcc / docker / cloud build), resolve this string to provider
    objects at the registry boundary -- the registry interface stays
    unchanged, so this model needs no migration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    type: CapabilityType
    input_schema: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)
    permission: Permission
    enabled: bool = True
    backend: str = Field(min_length=1, max_length=128, default="in_process")
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _VERSION_PATTERN.match(value):
            raise ValueError(
                "version must match MAJOR.MINOR[.PATCH], e.g. '1.0' or '2.1.3'"
            )
        return value

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


__all__ = ["Capability", "CapabilityType"]
