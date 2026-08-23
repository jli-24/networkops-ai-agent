"""Structured approval objects for embedded operations.

Enterprise approval approves *how the AI intends to operate* (an execution
plan), not just a bare action name: what to flash, on which device, from
which artifact, following which steps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from network_agent_rag.policy import PolicyRiskLevel


class ApprovalAction(StrEnum):
    FIRMWARE_FLASH = "firmware_flash"
    OTA_UPGRADE = "ota_upgrade"
    CONFIG_UPDATE = "config_update"
    SIMULATION_RUN = "simulation_run"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    action: ApprovalAction
    target: str = Field(min_length=1)
    risk_level: PolicyRiskLevel
    artifact_ref: str | None = None
    execution_plan: tuple[str, ...] = Field(min_length=1)
    details: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None

    @field_validator("execution_plan")
    @classmethod
    def reject_blank_steps(cls, steps: tuple[str, ...]) -> tuple[str, ...]:
        if any(not step.strip() for step in steps):
            raise ValueError("execution_plan steps must not be blank")
        return steps

    def with_created_at(self, now: datetime) -> "ApprovalRequest":
        return ApprovalRequest(
            **{
                **self.model_dump(),
                "created_at": now.astimezone(timezone.utc),
            }
        )


__all__ = ["ApprovalAction", "ApprovalRequest"]
