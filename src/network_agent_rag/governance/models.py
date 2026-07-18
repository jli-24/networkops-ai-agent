"""Strict, immutable contracts for governance projections."""

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
)

from network_agent_rag.auth import UserIdentity
from network_agent_rag.observability.models import TimelineEvent


Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class GovernanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecurityEventType(StrEnum):
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHORIZATION_DENIED = "authorization_denied"
    POLICY_VIOLATION = "policy_violation"
    APPROVAL_REQUIRED = "approval_required"
    HIGH_RISK_OPERATION = "high_risk_operation"
    REPAIR_FAILED = "repair_failed"
    SESSION_REVOKED = "session_revoked"


class SecurityEventSource(StrEnum):
    AUDIT = "audit"
    TRACE = "trace"


class GovernanceSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GovernanceOperation(StrEnum):
    VIEW_DEVICE_STATUS = "view_device_status"
    CREATE_REPAIR_PLAN = "create_repair_plan"
    RESTART_DEVICE = "restart_device"
    BATCH_CONFIGURATION_CHANGE = "batch_configuration_change"


class GovernanceRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComplianceReportType(StrEnum):
    OPERATION_AUDIT = "operation_audit"
    INCIDENT_HANDLING = "incident_handling"
    SECURITY_EVENT = "security_event"


class SecurityEvent(GovernanceModel):
    event_id: Identifier
    incident_id: Identifier
    source_event_id: Identifier
    event_type: SecurityEventType
    actor_id: Identifier
    source: SecurityEventSource
    action: Identifier
    decision: Identifier
    severity: GovernanceSeverity
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class RiskRequest(GovernanceModel):
    incident_id: Identifier
    identity: UserIdentity
    action: Identifier
    operation: GovernanceOperation
    devices: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("devices")
    @classmethod
    def reject_duplicate_devices(cls, devices: tuple[str, ...]) -> tuple[str, ...]:
        if len(devices) != len(set(devices)):
            raise ValueError("devices must not contain duplicates")
        return devices


class RiskAssessment(GovernanceModel):
    assessment_id: Identifier
    incident_id: Identifier
    actor_id: Identifier
    risk_score: int = Field(ge=0, le=100)
    risk_level: GovernanceRiskLevel
    reasons: tuple[str, ...]
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class ComplianceEvidence(GovernanceModel):
    source_event_id: Identifier
    actor_id: Identifier
    action: Identifier
    outcome: Identifier
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class ComplianceRiskSummary(GovernanceModel):
    assessment_count: int = Field(ge=0)
    latest_score: int | None = Field(default=None, ge=0, le=100)
    latest_level: GovernanceRiskLevel | None = None


class ComplianceReport(GovernanceModel):
    report_id: Identifier
    report_type: ComplianceReportType
    incident_id: Identifier
    generated_at: AwareDatetime
    timeline: tuple[TimelineEvent, ...]
    actors: tuple[str, ...]
    actions: tuple[ComplianceEvidence, ...]
    approvals: tuple[ComplianceEvidence, ...]
    execution: tuple[ComplianceEvidence, ...]
    verification: tuple[ComplianceEvidence, ...]
    security_events: tuple[SecurityEvent, ...]
    risk_summary: ComplianceRiskSummary
    missing_evidence: tuple[str, ...]

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class GovernanceMetricsSnapshot(GovernanceModel):
    security_events_total: int = Field(ge=0)
    authorization_denied_total: int = Field(ge=0)
    high_risk_operations_total: int = Field(ge=0)
    compliance_reports_total: int = Field(ge=0)


__all__ = [
    "ComplianceEvidence",
    "ComplianceReport",
    "ComplianceReportType",
    "ComplianceRiskSummary",
    "GovernanceMetricsSnapshot",
    "GovernanceOperation",
    "GovernanceRiskLevel",
    "GovernanceSeverity",
    "RiskAssessment",
    "RiskRequest",
    "SecurityEvent",
    "SecurityEventSource",
    "SecurityEventType",
]
