"""Pure v0.13 scoring and metrics aggregation."""

from __future__ import annotations

import math
import unittest

from network_agent_rag.evaluation import (
    AgentEvaluationCase,
    EvaluationObservation,
    aggregate_evaluation_metrics,
    score_evaluation_case,
)
from tests.test_evaluation_models import case_payload, observation_payload


def evaluated(
    *,
    case_updates: dict[str, object] | None = None,
    observation_updates: dict[str, object] | None = None,
):
    raw_case = case_payload()
    raw_case.update(case_updates or {})
    raw_observation = observation_payload()
    raw_observation.update(observation_updates or {})
    return score_evaluation_case(
        AgentEvaluationCase.model_validate(raw_case),
        EvaluationObservation.model_validate(raw_observation),
    )


class EvaluationScoringTests(unittest.TestCase):
    def test_scores_rca_top_k_confidence_repair_policy_and_rag(self) -> None:
        result = evaluated(
            case_updates={
                "expected_root_causes": ("switch_port_failure", "cable_failure"),
                "expected_document_ids": ("doc-1", "doc-2"),
            },
            observation_updates={
                "root_cause_candidates": (
                    "routing_error",
                    "switch_port_failure",
                    "cable_failure",
                ),
                "confidence": 25,
                "retrieved_document_ids": ("doc-1", "irrelevant"),
                "tool_call_keys": ("status", "status", "logs"),
                "failed_tool_calls": 1,
            },
        )

        self.assertFalse(result.rca_match)
        self.assertFalse(result.top1_accuracy)
        self.assertTrue(result.top3_accuracy)
        self.assertEqual(result.confidence_alignment, 0.75)
        self.assertTrue(result.repair_match)
        self.assertTrue(result.policy_match)
        self.assertEqual(result.retrieval_hit_rate, 1.0)
        self.assertEqual(result.context_precision, 0.5)
        self.assertEqual(result.context_recall, 0.5)
        self.assertEqual(result.tool_calls, 3)
        self.assertEqual(result.failed_tool_calls, 1)
        self.assertEqual(result.duplicate_tools, 1)

    def test_scores_empty_rag_context_and_execution_safety(self) -> None:
        safe = evaluated(
            case_updates={"expected_document_ids": ()},
            observation_updates={"retrieved_document_ids": ()},
        )
        unsafe = evaluated(
            observation_updates={
                "unauthorized_execution_count": 1,
                "policy_bypass_count": 1,
                "wrong_tool_call_count": 1,
            }
        )

        self.assertEqual(safe.retrieval_hit_rate, 1.0)
        self.assertEqual(safe.context_precision, 1.0)
        self.assertEqual(safe.context_recall, 1.0)
        self.assertTrue(safe.execution_safety)
        self.assertFalse(unsafe.execution_safety)

    def test_failed_execution_or_missing_context_cannot_pass(self) -> None:
        failed_execution = evaluated(
            observation_updates={"execution_status": "failed"},
        )
        missing_context = evaluated(
            observation_updates={"retrieved_document_ids": ()},
        )

        self.assertFalse(failed_execution.success)
        self.assertFalse(missing_context.success)

    def test_execution_safety_enforces_policy_and_approval_outcomes(self) -> None:
        denied_but_executed = evaluated(
            case_updates={
                "expected_policy_decision": "DENY",
                "execution_expected": False,
            },
            observation_updates={"policy_decision": "DENY"},
        )
        approval_bypassed = evaluated(
            observation_updates={"approval_status": "not_required"},
        )

        self.assertFalse(denied_but_executed.execution_safety)
        self.assertFalse(approval_bypassed.execution_safety)

    def test_aggregates_rates_tool_counts_and_linear_latency_percentiles(self) -> None:
        first = evaluated(observation_updates={"workflow_latency_ms": 100})
        second = evaluated(
            observation_updates={
                "policy_decision": "DENY",
                "approval_status": "rejected",
                "execution_status": "blocked",
                "workflow_latency_ms": 300,
                "tool_call_keys": ("one", "one"),
                "failed_tool_calls": 1,
            }
        )
        third = evaluated(
            case_updates={
                "case_id": "NET003",
                "execution_expected": False,
                "expected_operations": (),
                "expected_policy_decision": "DENY",
            },
            observation_updates={
                "operation": (),
                "policy_decision": "DENY",
                "approval_status": "not_required",
                "execution_status": "not_executed",
                "workflow_latency_ms": 500,
                "tool_call_keys": (),
            },
        )

        metrics = aggregate_evaluation_metrics((first, second, third))

        self.assertEqual(metrics.benchmark_count, 1)
        self.assertEqual(metrics.total_cases, 3)
        self.assertEqual(metrics.repair_success_rate, 0.5)
        self.assertEqual(metrics.policy_block_rate, 0.5)
        self.assertEqual(metrics.blocked_rate, 0.5)
        self.assertEqual(metrics.approval_rate, 1.0)
        self.assertEqual(metrics.avg_latency, 300.0)
        self.assertEqual(metrics.p50_latency, 300.0)
        self.assertEqual(metrics.p95_latency, 480.0)
        self.assertEqual(metrics.avg_tool_calls, 1.0)
        self.assertEqual(metrics.failed_tool_calls, 1)
        self.assertEqual(metrics.duplicate_calls, 1)
        for value in metrics.model_dump().values():
            if isinstance(value, float):
                self.assertFalse(math.isnan(value))

    def test_empty_metrics_are_safe_and_finite(self) -> None:
        metrics = aggregate_evaluation_metrics(())

        self.assertEqual(metrics.total_cases, 0)
        self.assertEqual(metrics.benchmark_count, 0)
        self.assertTrue(all(value == 0 for value in metrics.model_dump().values()))


if __name__ == "__main__":
    unittest.main()
