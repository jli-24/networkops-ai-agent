"""Offline, deterministic benchmark runner."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from uuid import uuid4
import json

from pydantic import ValidationError

from network_agent_rag.evaluation.models import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkObservation,
    BenchmarkRunResult,
)


ObservationCallback = Callable[[BenchmarkCase], BenchmarkObservation | dict[str, object]]


def load_benchmark_dataset(path: str | Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    identifiers: set[str] = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            case = BenchmarkCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValueError(f"invalid benchmark case on line {line_number}") from error
        if case.confidence_min > case.confidence_max:
            raise ValueError(f"invalid confidence range on line {line_number}")
        if case.case_id in identifiers:
            raise ValueError(f"duplicate benchmark case_id: {case.case_id}")
        identifiers.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("benchmark dataset must contain at least one case")
    return cases


class BenchmarkRunner:
    def __init__(
        self,
        observe: ObservationCallback,
        *,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.observe = observe
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.run_id_factory = run_id_factory or (lambda: uuid4().hex)

    def run(
        self,
        cases: Sequence[BenchmarkCase],
        *,
        dataset_name: str,
        dataset_version: str,
    ) -> BenchmarkRunResult:
        if not cases:
            raise ValueError("benchmark cases must not be empty")
        started = _aware(self.clock())
        results: list[BenchmarkCaseResult] = []
        for case in cases:
            try:
                observation = BenchmarkObservation.model_validate(self.observe(case))
            except (ValidationError, TypeError, ValueError) as error:
                raise ValueError(f"invalid observation for case {case.case_id}") from error
            results.append(_score(case, observation))
        finished = _aware(self.clock())
        return BenchmarkRunResult(
            run_id=_required(self.run_id_factory(), "run_id"),
            dataset_name=_required(dataset_name, "dataset_name"),
            dataset_version=_required(dataset_version, "dataset_version"),
            started_at=started,
            finished_at=finished,
            cases=results,
            summary=_summary(results),
        )


def _score(case: BenchmarkCase, observation: BenchmarkObservation) -> BenchmarkCaseResult:
    required = set(case.required_evidence_refs)
    evidence_recall = (
        len(required & set(observation.evidence_refs)) / len(required) if required else 1.0
    )
    groundedness = (
        observation.grounded_claims / observation.total_claims
        if observation.total_claims
        else 1.0
    )
    checks = {
        "route_correct": observation.route == case.expected_route,
        "root_cause_correct": observation.root_cause.casefold() == case.expected_root_cause.casefold(),
        "confidence_in_range": case.confidence_min <= observation.confidence_percent <= case.confidence_max,
        "approval_correct": observation.approval_required == case.approval_required,
        "execution_status_correct": observation.execution_status == case.expected_execution_status,
        "safe_execution": observation.unsafe_execution_claims == 0,
    }
    passed = all(checks.values()) and evidence_recall == 1.0 and groundedness == 1.0
    return BenchmarkCaseResult(
        case_id=case.case_id,
        passed=passed,
        evidence_recall=round(evidence_recall, 4),
        groundedness=round(groundedness, 4),
        duration_ms=observation.duration_ms,
        quality_iterations=observation.quality_iterations,
        **checks,
    )


def _summary(results: list[BenchmarkCaseResult]) -> dict[str, int | float]:
    total = len(results)
    durations = sorted(item.duration_ms for item in results)
    return {
        "cases_total": total,
        "cases_passed": sum(item.passed for item in results),
        "pass_rate": round(sum(item.passed for item in results) / total, 4),
        "route_accuracy": round(sum(item.route_correct for item in results) / total, 4),
        "evidence_recall": round(sum(item.evidence_recall for item in results) / total, 4),
        "root_cause_accuracy": round(sum(item.root_cause_correct for item in results) / total, 4),
        "approval_accuracy": round(sum(item.approval_correct for item in results) / total, 4),
        "groundedness": round(sum(item.groundedness for item in results) / total, 4),
        "safe_execution_rate": round(sum(item.safe_execution for item in results) / total, 4),
        "duration_ms_mean": round(sum(durations) / total, 3),
        "duration_ms_p50": _percentile(durations, 0.50),
        "duration_ms_p95": _percentile(durations, 0.95),
        "quality_iterations_mean": round(sum(item.quality_iterations for item in results) / total, 3),
    }


def _percentile(values: list[float], fraction: float) -> float:
    return float(values[max(0, ceil(fraction * len(values)) - 1)])


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("benchmark clock must return a timezone-aware datetime")
    return value


__all__ = ["BenchmarkRunner", "ObservationCallback", "load_benchmark_dataset"]
