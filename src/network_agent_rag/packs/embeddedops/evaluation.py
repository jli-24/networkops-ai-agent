"""Embedded-domain evaluation cases (fault diagnosis benchmarks).

These cases benchmark the Debug Agent / RAG loop against known embedded
failure modes. They intentionally use a simpler schema than the network
``AgentEvaluationCase``: an input symptom plus expected diagnostic hints.
Agent Analytics reads this dataset as its data source.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmbeddedCaseCategory(StrEnum):
    SPI_FAILURE = "spi_failure"
    I2C_FAILURE = "i2c_failure"
    FREERTOS_DEADLOCK = "freertos_deadlock"
    WIFI_FAILURE = "wifi_failure"
    OTA_FAILURE = "ota_failure"


class EmbeddedEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str = Field(min_length=1)
    case_id: str = Field(min_length=1, max_length=32)
    category: EmbeddedCaseCategory
    input: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    expected_keywords: tuple[str, ...] = Field(min_length=1)

    @field_validator("input", "expected")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text fields must not be blank")
        return value.strip()


CASES_DIRECTORY = Path(__file__).resolve().parent / "evaluation_cases"


def load_embedded_cases(
    directory: str | Path | None = None,
    expected_version: str | None = None,
) -> tuple[EmbeddedEvaluationCase, ...]:
    """Load every ``*.json`` case in ``directory`` with strict validation."""

    directory = directory or CASES_DIRECTORY

    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"embedded cases directory not found: {root}")
    files = sorted(root.glob("*.json"))
    if not files:
        raise ValueError("embedded cases directory contains no cases")
    cases: list[EmbeddedEvaluationCase] = []
    identifiers: set[str] = set()
    versions: set[str] = set()
    for file in files:
        case = EmbeddedEvaluationCase.model_validate(
            json.loads(file.read_text(encoding="utf-8"))
        )
        if case.case_id in identifiers:
            raise ValueError(f"duplicate embedded case_id: {case.case_id}")
        identifiers.add(case.case_id)
        versions.add(case.dataset_version)
        cases.append(case)
    if len(versions) != 1:
        raise ValueError("embedded dataset_version must be consistent")
    if expected_version is not None and next(iter(versions)) != expected_version:
        raise ValueError("embedded dataset does not match expected version")
    return tuple(sorted(cases, key=lambda item: item.case_id))


__all__ = [
    "CASES_DIRECTORY",
    "EmbeddedCaseCategory",
    "EmbeddedEvaluationCase",
    "load_embedded_cases",
]
