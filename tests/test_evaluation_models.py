"""Strict contracts for the v0.13 offline evaluation layer."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from network_agent_rag.evaluation import (
    AgentEvaluationCase,
    EvaluationCategory,
    EvaluationObservation,
)
from network_agent_rag.policy import PolicyEffect, PolicyOperation, PolicyRiskLevel


def case_payload() -> dict[str, object]:
    return {
        "dataset_version": " v1 ",
        "case_id": " NET001 ",
        "category": "connectivity_failure",
        "fault_description": " PC cannot reach server ",
        "expected_root_causes": (" switch_port_failure ",),
        "expected_operations": ("restart_device",),
        "expected_risk_level": "high",
        "expected_policy_decision": "REQUIRE_APPROVAL",
        "expected_document_ids": (" runbook:switch-port ",),
        "execution_expected": True,
    }


def observation_payload() -> dict[str, object]:
    return {
        "root_cause_candidates": (" switch_port_failure ",),
        "confidence": 90.0,
        "operation": ("restart_device",),
        "risk_level": "high",
        "policy_decision": "REQUIRE_APPROVAL",
        "retrieved_document_ids": (" runbook:switch-port ",),
        "tool_call_keys": (" status:sw1 ",),
        "failed_tool_calls": 0,
        "approval_status": "approved",
        "execution_status": "succeeded",
        "workflow_latency_ms": 125.5,
        "unauthorized_execution_count": 0,
        "policy_bypass_count": 0,
        "wrong_tool_call_count": 0,
        "error_code": None,
    }


class EvaluationModelTests(unittest.TestCase):
    def test_case_is_frozen_strict_and_normalizes_string_collections(self) -> None:
        case = AgentEvaluationCase.model_validate(case_payload())

        self.assertEqual(case.dataset_version, "v1")
        self.assertEqual(case.case_id, "NET001")
        self.assertEqual(case.fault_description, "PC cannot reach server")
        self.assertEqual(case.expected_root_causes, ("switch_port_failure",))
        self.assertEqual(case.expected_document_ids, ("runbook:switch-port",))
        self.assertEqual(case.category, EvaluationCategory.CONNECTIVITY_FAILURE)
        self.assertEqual(case.expected_operations, (PolicyOperation.RESTART_DEVICE,))
        self.assertEqual(case.expected_risk_level, PolicyRiskLevel.HIGH)
        self.assertEqual(
            case.expected_policy_decision,
            PolicyEffect.REQUIRE_APPROVAL,
        )
        with self.assertRaises(ValidationError):
            case.case_id = "changed"  # type: ignore[misc]

    def test_case_rejects_duplicates_invalid_execution_contract_and_extra_fields(self) -> None:
        for field in (
            "expected_root_causes",
            "expected_operations",
            "expected_document_ids",
        ):
            payload = case_payload()
            payload[field] = ("restart_device", "restart_device") if field == "expected_operations" else ("same", "same")
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AgentEvaluationCase.model_validate(payload)

        missing_action = case_payload()
        missing_action["expected_operations"] = ()
        with self.assertRaises(ValidationError):
            AgentEvaluationCase.model_validate(missing_action)

        denied_execution = case_payload()
        denied_execution["expected_policy_decision"] = "DENY"
        denied_execution["execution_expected"] = True
        with self.assertRaises(ValidationError):
            AgentEvaluationCase.model_validate(denied_execution)

        extra = case_payload()
        extra["prompt"] = "must not be accepted"
        with self.assertRaises(ValidationError):
            AgentEvaluationCase.model_validate(extra)

    def test_observation_validates_safe_enums_counts_and_fixed_error_codes(self) -> None:
        observation = EvaluationObservation.model_validate(observation_payload())

        self.assertEqual(observation.root_cause_candidates, ("switch_port_failure",))
        self.assertEqual(observation.tool_call_keys, ("status:sw1",))
        self.assertEqual(observation.operation, (PolicyOperation.RESTART_DEVICE,))
        with self.assertRaises(ValidationError):
            observation.confidence = 10  # type: ignore[misc]

        invalid_values = (
            ("confidence", 101),
            ("approval_status", "waiting"),
            ("execution_status", "executed"),
            ("error_code", "raw exception text"),
            ("failed_tool_calls", 2),
        )
        for field, value in invalid_values:
            payload = observation_payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                EvaluationObservation.model_validate(payload)

        extra = observation_payload()
        extra["stack_trace"] = "secret"
        with self.assertRaises(ValidationError):
            EvaluationObservation.model_validate(extra)

    def test_models_reject_implicit_scalar_coercion(self) -> None:
        invalid_case = case_payload()
        invalid_case["execution_expected"] = 1
        with self.assertRaises(ValidationError):
            AgentEvaluationCase.model_validate(invalid_case)

        for field, value in (
            ("confidence", "90"),
            ("failed_tool_calls", False),
            ("workflow_latency_ms", "125.5"),
        ):
            payload = observation_payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                EvaluationObservation.model_validate(payload)

    def test_all_evaluation_categories_are_fixed(self) -> None:
        self.assertEqual(
            {item.value for item in EvaluationCategory},
            {
                "connectivity_failure",
                "device_failure",
                "service_failure",
                "performance_issue",
                "security_event",
            },
        )


if __name__ == "__main__":
    unittest.main()
