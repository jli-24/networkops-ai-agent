"""Artifact contracts with content integrity hashes."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ArtifactType(StrEnum):
    HARDWARE_DESIGN = "hardware_design"
    FIRMWARE_SOURCE = "firmware_source"
    FIRMWARE_BINARY = "firmware_binary"
    COMPILE_LOG = "compile_log"
    SIMULATION_LOG = "simulation_log"
    REPORT = "report"


class Artifact(BaseModel):
    """A stored deliverable, hashed so consumers can verify integrity.

    Future OTA flows rely on this: download -> sha256 check -> upgrade.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    type: ArtifactType
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)

    @field_validator("name", "version")
    @classmethod
    def reject_path_separators(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("name/version must not contain path separators")
        return value


__all__ = ["Artifact", "ArtifactType"]
