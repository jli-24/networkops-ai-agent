"""Versioned JSONL loading for v0.13 evaluation cases."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.evaluation import load_evaluation_dataset
from network_agent_rag.evaluation.models import EvaluationCategory
from tests.test_evaluation_models import case_payload


class EvaluationDatasetTests(unittest.TestCase):
    def test_repository_dataset_covers_all_categories(self) -> None:
        cases = load_evaluation_dataset(
            Path("src/network_agent_rag/packs/networkops/evaluation/network_fault_v1.jsonl"),
            expected_version="v1",
        )

        self.assertEqual(len(cases), 5)
        self.assertEqual(
            {case.category for case in cases},
            set(EvaluationCategory),
        )

    def test_loads_valid_jsonl_and_returns_stable_case_order(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            second = case_payload()
            second["case_id"] = "NET002"
            first = case_payload()
            path.write_text(
                "\n".join(json.dumps(item) for item in (second, first)),
                encoding="utf-8",
            )

            cases = load_evaluation_dataset(path, expected_version="v1")

            self.assertEqual(tuple(item.case_id for item in cases), ("NET001", "NET002"))

    def test_rejects_version_mismatch_duplicate_case_and_invalid_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            first = case_payload()
            second = case_payload()
            second["case_id"] = "NET002"
            second["dataset_version"] = "v2"
            path.write_text(
                "\n".join(json.dumps(item) for item in (first, second)),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "dataset_version"):
                load_evaluation_dataset(path)

            path.write_text(
                "\n".join(json.dumps(first) for _ in range(2)),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_evaluation_dataset(path)

            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                load_evaluation_dataset(path)

    def test_rejects_empty_dataset_and_expected_version_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least one"):
                load_evaluation_dataset(path)

            path.write_text(json.dumps(case_payload()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected version"):
                load_evaluation_dataset(path, expected_version="v2")


if __name__ == "__main__":
    unittest.main()
