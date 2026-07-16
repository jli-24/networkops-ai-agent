"""Deterministic benchmark data contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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


__all__ = [
    "BenchmarkCase",
    "BenchmarkCaseResult",
    "BenchmarkObservation",
    "BenchmarkRunResult",
]
