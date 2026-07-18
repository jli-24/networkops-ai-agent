"""Deterministic benchmark data contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from network_agent_rag.policy import PolicyEffect, PolicyOperation, PolicyRiskLevel


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkCase(_StrictModel):
    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_route: list[str]
    required_evidence_refs: list[str]
    expected_root_cause: str = Field(min_length=1)
    confidence_min: float = Field(ge=0, le=100)
    confidence_max: float = Field(ge=0, le=100)
    approval_required: bool
    expected_execution_status: str = Field(min_length=1)


class BenchmarkObservation(_StrictModel):
    route: list[str]
    evidence_refs: list[str]
    root_cause: str
    confidence_percent: float = Field(ge=0, le=100)
    approval_required: bool
    execution_status: str
    grounded_claims: int = Field(ge=0)
    total_claims: int = Field(ge=0)
    unsafe_execution_claims: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    quality_iterations: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_claim_counts(self) -> "BenchmarkObservation":
        if self.grounded_claims > self.total_claims:
            raise ValueError("grounded_claims cannot exceed total_claims")
        return self


class BenchmarkCaseResult(_StrictModel):
    case_id: str
    passed: bool
    route_correct: bool
    evidence_recall: float = Field(ge=0, le=1)
    root_cause_correct: bool
    confidence_in_range: bool
    approval_correct: bool
    execution_status_correct: bool
    groundedness: float = Field(ge=0, le=1)
    safe_execution: bool
    duration_ms: float = Field(ge=0)
    quality_iterations: int = Field(ge=0)


class BenchmarkRunResult(_StrictModel):
    run_id: str
    dataset_name: str
    dataset_version: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    cases: list[BenchmarkCaseResult]
    summary: dict[str, int | float]


CleanString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]
Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class _FrozenEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvaluationCategory(StrEnum):
    CONNECTIVITY_FAILURE = "connectivity_failure"
    DEVICE_FAILURE = "device_failure"
    SERVICE_FAILURE = "service_failure"
    PERFORMANCE_ISSUE = "performance_issue"
    SECURITY_EVENT = "security_event"


class AgentEvaluationCase(_FrozenEvaluationModel):
    dataset_version: Identifier
    case_id: Identifier
    category: EvaluationCategory
    fault_description: CleanString
    expected_root_causes: tuple[CleanString, ...] = Field(min_length=1)
    expected_operations: tuple[PolicyOperation, ...]
    expected_risk_level: PolicyRiskLevel
    expected_policy_decision: PolicyEffect
    expected_document_ids: tuple[Identifier, ...]
    execution_expected: bool

    @field_validator("category", mode="before")
    @classmethod
    def parse_category(cls, value: object) -> object:
        return _parse_enum(value, EvaluationCategory)

    @field_validator("expected_operations", mode="before")
    @classmethod
    def parse_expected_operations(cls, value: object) -> object:
        return _parse_enum_tuple(value, PolicyOperation)

    @field_validator("expected_risk_level", mode="before")
    @classmethod
    def parse_expected_risk_level(cls, value: object) -> object:
        return _parse_enum(value, PolicyRiskLevel)

    @field_validator("expected_policy_decision", mode="before")
    @classmethod
    def parse_expected_policy_decision(cls, value: object) -> object:
        return _parse_enum(value, PolicyEffect)

    @field_validator(
        "expected_root_causes",
        "expected_operations",
        "expected_document_ids",
    )
    @classmethod
    def reject_duplicate_expected_values(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        if len(values) != len(set(values)):
            raise ValueError("expected values must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_execution_expectation(self) -> "AgentEvaluationCase":
        if self.execution_expected and not self.expected_operations:
            raise ValueError("execution_expected cases require expected_operations")
        if self.execution_expected and self.expected_policy_decision is PolicyEffect.DENY:
            raise ValueError("DENY cases cannot expect execution")
        return self


ApprovalStatus = Literal["not_required", "required", "approved", "rejected"]
ExecutionStatus = Literal["succeeded", "failed", "blocked", "not_executed"]
EvaluationErrorCode = Literal[
    "EVALUATION_SOURCE_FAILED",
    "INVALID_OBSERVATION",
]


class EvaluationObservation(_FrozenEvaluationModel):
    root_cause_candidates: tuple[CleanString, ...]
    confidence: float = Field(ge=0, le=100)
    operation: tuple[PolicyOperation, ...]
    risk_level: PolicyRiskLevel
    policy_decision: PolicyEffect
    retrieved_document_ids: tuple[Identifier, ...]
    tool_call_keys: tuple[Identifier, ...]
    failed_tool_calls: int = Field(ge=0)
    approval_status: ApprovalStatus
    execution_status: ExecutionStatus
    workflow_latency_ms: float = Field(ge=0)
    unauthorized_execution_count: int = Field(ge=0)
    policy_bypass_count: int = Field(ge=0)
    wrong_tool_call_count: int = Field(ge=0)
    error_code: EvaluationErrorCode | None = None

    @field_validator("operation", mode="before")
    @classmethod
    def parse_operations(cls, value: object) -> object:
        return _parse_enum_tuple(value, PolicyOperation)

    @field_validator("risk_level", mode="before")
    @classmethod
    def parse_risk_level(cls, value: object) -> object:
        return _parse_enum(value, PolicyRiskLevel)

    @field_validator("policy_decision", mode="before")
    @classmethod
    def parse_policy_decision(cls, value: object) -> object:
        return _parse_enum(value, PolicyEffect)

    @field_validator(
        "root_cause_candidates",
        "operation",
        "retrieved_document_ids",
    )
    @classmethod
    def reject_duplicate_observation_values(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        if len(values) != len(set(values)):
            raise ValueError("observation values must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_tool_counts(self) -> "EvaluationObservation":
        if self.failed_tool_calls > len(self.tool_call_keys):
            raise ValueError("failed_tool_calls cannot exceed tool calls")
        return self


class EvaluationResult(_FrozenEvaluationModel):
    case_id: Identifier
    execution_expected: bool
    success: bool
    rca_match: bool
    repair_match: bool
    policy_match: bool
    top1_accuracy: bool
    top3_accuracy: bool
    confidence_alignment: float = Field(ge=0, le=1)
    retrieval_hit_rate: float = Field(ge=0, le=1)
    context_precision: float = Field(ge=0, le=1)
    context_recall: float = Field(ge=0, le=1)
    repair_success: bool
    policy_blocked: bool
    approval_required: bool
    blocked: bool
    execution_safety: bool
    tool_calls: int = Field(ge=0)
    failed_tool_calls: int = Field(ge=0)
    duplicate_tools: int = Field(ge=0)
    latency: float = Field(ge=0)
    error_code: EvaluationErrorCode | None = None


class EvaluationMetricsSnapshot(_FrozenEvaluationModel):
    benchmark_count: int = Field(ge=0)
    total_cases: int = Field(ge=0)
    rca_accuracy: float = Field(ge=0, le=1)
    top1_accuracy: float = Field(ge=0, le=1)
    top3_accuracy: float = Field(ge=0, le=1)
    confidence_alignment: float = Field(ge=0, le=1)
    retrieval_hit_rate: float = Field(ge=0, le=1)
    context_precision: float = Field(ge=0, le=1)
    context_recall: float = Field(ge=0, le=1)
    repair_success_rate: float = Field(ge=0, le=1)
    policy_block_rate: float = Field(ge=0, le=1)
    blocked_rate: float = Field(ge=0, le=1)
    approval_rate: float = Field(ge=0, le=1)
    execution_safety_rate: float = Field(ge=0, le=1)
    avg_latency: float = Field(ge=0)
    p50_latency: float = Field(ge=0)
    p95_latency: float = Field(ge=0)
    avg_tool_calls: float = Field(ge=0)
    failed_tool_calls: int = Field(ge=0)
    duplicate_calls: int = Field(ge=0)


class BenchmarkReport(_FrozenEvaluationModel):
    report_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    dataset_version: Identifier
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    metrics: EvaluationMetricsSnapshot
    results: tuple[EvaluationResult, ...]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_case_counts(self) -> "BenchmarkReport":
        if self.total_cases != len(self.results):
            raise ValueError("total_cases must match results")
        if self.total_cases != self.passed_cases + self.failed_cases:
            raise ValueError("case counts must add up")
        return self


def _parse_enum(value: object, enum_type: type[StrEnum]) -> object:
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            return value
    return value


def _parse_enum_tuple(value: object, enum_type: type[StrEnum]) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(_parse_enum(item, enum_type) for item in value)
    return value


__all__ = [
    "AgentEvaluationCase",
    "ApprovalStatus",
    "BenchmarkCase",
    "BenchmarkCaseResult",
    "BenchmarkObservation",
    "BenchmarkReport",
    "BenchmarkRunResult",
    "EvaluationCategory",
    "EvaluationErrorCode",
    "EvaluationMetricsSnapshot",
    "EvaluationObservation",
    "EvaluationResult",
    "ExecutionStatus",
]
