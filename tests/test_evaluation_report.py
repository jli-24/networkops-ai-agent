"""Stable, in-memory v0.13 evaluation reports."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from network_agent_rag.evaluation import (
    AgentEvaluationCase,
    AgentEvaluationRunner,
    EvaluationReportBuilder,
)
from tests.test_evaluation_models import case_payload, observation_payload


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


class EvaluationReportTests(unittest.TestCase):
    def _report(self):
        runner = AgentEvaluationRunner(
            lambda _case: observation_payload(),
            clock=lambda: NOW,
        )
        return runner.run_dataset(
            (AgentEvaluationCase.model_validate(case_payload()),),
            dataset_version="v1",
        )

    def test_json_and_markdown_outputs_are_stable_and_consistent(self) -> None:
        report = self._report()
        builder = EvaluationReportBuilder()

        first_json = builder.to_json(report)
        second_json = builder.to_json(report)
        first_markdown = builder.to_markdown(report)
        second_markdown = builder.to_markdown(report)

        self.assertEqual(first_json, second_json)
        self.assertEqual(first_markdown, second_markdown)
        payload = json.loads(first_json)
        self.assertEqual(payload["report_id"], report.report_id)
        self.assertEqual(payload["metrics"]["total_cases"], 1)
        self.assertIn("# NetworkOps AI Agent Evaluation v1", first_markdown)
        self.assertIn(report.report_id, first_markdown)

    def test_report_contains_no_prompt_exception_or_user_payload_fields(self) -> None:
        rendered = EvaluationReportBuilder().to_json(self._report()).casefold()

        for forbidden in ("prompt", "traceback", "stack_trace", "user_data"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
