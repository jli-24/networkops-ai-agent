"""Versioned JSONL datasets for the offline v2 evaluation layer."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from network_agent_rag.evaluation.models import AgentEvaluationCase


def load_evaluation_dataset(
    path: str | Path,
    expected_version: str | None = None,
) -> tuple[AgentEvaluationCase, ...]:
    cases: list[AgentEvaluationCase] = []
    identifiers: set[str] = set()
    versions: set[str] = set()
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = AgentEvaluationCase.model_validate_json(line)
        except ValidationError as error:
            raise ValueError(f"invalid evaluation case on line {line_number}") from error
        if case.case_id in identifiers:
            raise ValueError(f"duplicate evaluation case_id: {case.case_id}")
        identifiers.add(case.case_id)
        versions.add(case.dataset_version)
        cases.append(case)
    if not cases:
        raise ValueError("evaluation dataset must contain at least one case")
    if len(versions) != 1:
        raise ValueError("evaluation dataset_version must be consistent")
    version = next(iter(versions))
    if expected_version is not None and version != expected_version.strip():
        raise ValueError("evaluation dataset does not match expected version")
    return tuple(sorted(cases, key=lambda item: item.case_id))


__all__ = ["load_evaluation_dataset"]
