"""Embedded evaluation case dataset tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from network_agent_rag.packs.embeddedops.evaluation import (
    EmbeddedEvaluationCase,
    load_embedded_cases,
)


def _case(**overrides) -> dict:
    payload = {
        "dataset_version": "embedded_v1",
        "case_id": "EMB100",
        "category": "spi_failure",
        "input": "spi broken",
        "expected": "check CPOL/CPHA",
        "expected_keywords": ["CPOL"],
    }
    payload.update(overrides)
    return payload


class EmbeddedEvaluationCaseTests(unittest.TestCase):
    def test_repository_dataset_loads(self) -> None:
        cases = load_embedded_cases(expected_version="embedded_v1")
        self.assertGreaterEqual(len(cases), 3)
        categories = {case.category.value for case in cases}
        self.assertIn("wifi_failure", categories)
        self.assertIn("freertos_deadlock", categories)
        for case in cases:
            self.assertTrue(case.expected_keywords)

    def test_strict_validation(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddedEvaluationCase.model_validate(_case(input=" "))
        with self.assertRaises(ValueError):
            EmbeddedEvaluationCase.model_validate(_case(category="nope"))

    def test_loader_rejects_duplicates_and_mixed_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(
                json.dumps(_case()), encoding="utf-8"
            )
            (root / "b.json").write_text(
                json.dumps(_case()), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_embedded_cases(root)

            (root / "b.json").write_text(
                json.dumps(_case(case_id="EMB101", dataset_version="embedded_v2")),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_embedded_cases(root)

    def test_loader_missing_directory(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_embedded_cases("does/not/exist")

    def test_version_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            load_embedded_cases(expected_version="other")


if __name__ == "__main__":
    unittest.main()
