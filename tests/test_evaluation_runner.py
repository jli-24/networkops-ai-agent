"""Offline-only v0.13 evaluation runner."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from network_agent_rag.evaluation import (
    AgentEvaluationCase,
    AgentEvaluationRunner,
    BenchmarkCase,
    BenchmarkRunner,
)
from tests.test_benchmark_evaluation import case_payload as legacy_case_payload
from tests.test_evaluation_models import case_payload, observation_payload


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


class EvaluationRunnerTests(unittest.TestCase):
    def test_run_case_calls_only_injected_observer(self) -> None:
        calls: list[str] = []
        runner = AgentEvaluationRunner(
            lambda case: calls.append(case.case_id) or observation_payload(),
            clock=lambda: NOW,
        )

        result = runner.run_case(AgentEvaluationCase.model_validate(case_payload()))

        self.assertEqual(calls, ["NET001"])
        self.assertTrue(result.success)
        self.assertIsNone(result.error_code)

    def test_callback_and_invalid_observation_use_fixed_error_codes(self) -> None:
        case = AgentEvaluationCase.model_validate(case_payload())

        def raises(_case):
            raise RuntimeError("secret prompt and user data")

        source_failure = AgentEvaluationRunner(raises).run_case(case)
        invalid = AgentEvaluationRunner(lambda _case: {"prompt": "private"}).run_case(case)

        self.assertEqual(source_failure.error_code, "EVALUATION_SOURCE_FAILED")
        self.assertEqual(invalid.error_code, "INVALID_OBSERVATION")
        self.assertNotIn("secret", repr(source_failure))
        self.assertNotIn("private", repr(invalid))

    def test_dataset_report_id_is_stable_and_results_are_sorted(self) -> None:
        first = case_payload()
        second = case_payload()
        second["case_id"] = "NET002"
        cases = tuple(
            AgentEvaluationCase.model_validate(payload)
            for payload in (second, first)
        )
        runner = AgentEvaluationRunner(lambda _case: observation_payload(), clock=lambda: NOW)

        report_one = runner.run_dataset(cases, dataset_version="v1")
        report_two = runner.run_dataset(reversed(cases), dataset_version="v1")

        self.assertEqual(report_one.report_id, report_two.report_id)
        self.assertEqual(
            tuple(item.case_id for item in report_one.results),
            ("NET001", "NET002"),
        )
        self.assertEqual(report_one.total_cases, 2)
        self.assertEqual(report_one.passed_cases, 2)

    def test_rejects_empty_or_version_mismatched_dataset(self) -> None:
        runner = AgentEvaluationRunner(lambda _case: observation_payload())
        with self.assertRaisesRegex(ValueError, "at least one"):
            runner.run_dataset((), dataset_version="v1")
        with self.assertRaisesRegex(ValueError, "dataset_version"):
            runner.run_dataset(
                (AgentEvaluationCase.model_validate(case_payload()),),
                dataset_version="v2",
            )

    def test_legacy_runner_public_contract_remains_available(self) -> None:
        case = BenchmarkCase.model_validate(legacy_case_payload("LEGACY-1"))
        self.assertTrue(callable(BenchmarkRunner))
        self.assertEqual(case.case_id, "LEGACY-1")


if __name__ == "__main__":
    unittest.main()
