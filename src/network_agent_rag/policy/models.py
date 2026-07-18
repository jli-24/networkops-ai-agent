"""Immutable contracts for deterministic execution policies."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from network_agent_rag.auth import Permission, Role


Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyEffect(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class PolicyOperation(StrEnum):
    VIEW_DEVICE_STATUS = "view_device_status"
    CREATE_REPAIR_PLAN = "create_repair_plan"
    RESTART_DEVICE = "restart_device"
    BATCH_CONFIGURATION_CHANGE = "batch_configuration_change"
    UNKNOWN = "unknown"


class PolicyRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyContext(PolicyModel):
    actor_id: Identifier
    roles: tuple[Role, ...] = Field(min_length=1)
    permission: Permission
    operation: PolicyOperation
    tool_name: Identifier
    devices: tuple[Identifier, ...] = Field(min_length=1)
    risk_level: PolicyRiskLevel
    timestamp: AwareDatetime

    @field_validator("roles", "devices")
    @classmethod
    def reject_duplicates(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        if len(values) != len(set(values)):
            raise ValueError("values must not contain duplicates")
        return values

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class PolicyCondition(PolicyModel):
    operation: frozenset[PolicyOperation] | None = None
    risk_level: frozenset[PolicyRiskLevel] | None = None
    permission: frozenset[Permission] | None = None
    role: frozenset[Role] | None = None
    min_devices: int | None = Field(default=None, ge=0)
    max_devices: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_constraints(self) -> "PolicyCondition":
        if all(
            value is None
            for value in (
                self.operation,
                self.risk_level,
                self.permission,
                self.role,
                self.min_devices,
                self.max_devices,
            )
        ):
            raise ValueError("condition must contain at least one constraint")
        if (
            self.min_devices is not None
            and self.max_devices is not None
            and self.min_devices > self.max_devices
        ):
            raise ValueError("min_devices must not exceed max_devices")
        for value in (self.operation, self.risk_level, self.permission, self.role):
            if value is not None and not value:
                raise ValueError("condition sets must not be empty")
        return self


class PolicyRule(PolicyModel):
    rule_id: Identifier
    name: Identifier
    condition: PolicyCondition
    effect: PolicyEffect


class PolicyDecision(PolicyModel):
    decision_id: Annotated[
        str,
        StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    ]
    decision: PolicyEffect
    reasons: tuple[str, ...] = Field(min_length=1)
    matched_rules: tuple[Identifier, ...]
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


__all__ = [
    "PolicyCondition",
    "PolicyContext",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyOperation",
    "PolicyRiskLevel",
    "PolicyRule",
]
