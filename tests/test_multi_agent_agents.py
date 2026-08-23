"""Contract tests for individual v0.2 agent helpers."""

from __future__ import annotations

import unittest

from network_agent_rag.packs.networkops.agents.multi_agent.repair_agent import default_repair_plan
from network_agent_rag.packs.networkops.agents.multi_agent.state import validate_repair_plan
from network_agent_rag.packs.networkops.agents.multi_agent.supervisor import validate_supervisor_plan

from tests.test_multi_agent_workflow import incident_plan


class MultiAgentContractTests(unittest.TestCase):
    def test_supervisor_plan_requires_dependency_order_and_repair_consistency(self) -> None:
        validate_supervisor_plan(incident_plan())

        invalid = incident_plan(required_agents=["report", "diagnosis"])
        with self.assertRaisesRegex(ValueError, "report must be last"):
            validate_supervisor_plan(invalid)

        invalid = incident_plan(
            required_agents=["diagnosis", "report"],
            requires_repair=True,
        )
        with self.assertRaisesRegex(ValueError, "repair"):
            validate_supervisor_plan(invalid)

        invalid = incident_plan(
            required_agents=["diagnosis", "repair", "report"],
            required_sources=["topology", "monitoring", "knowledge"],
        )
        with self.assertRaisesRegex(ValueError, "topology evidence requires topology"):
            validate_supervisor_plan(invalid)

    def test_default_repair_plan_is_safe_and_requires_approval(self) -> None:
        plan = default_repair_plan(
            {
                "diagnosis_result": {
                    "summary": "SW1-SW2 光模块异常",
                    "hypotheses": [
                        {
                            "cause": "光模块异常",
                            "confidence_percent": 80,
                            "supporting_evidence": ["CRC 增长"],
                            "contradicting_evidence": [],
                        }
                    ],
                    "evidence_refs": ["METRIC-3001"],
                    "remaining_uncertainties": [],
                }
            }
        )

        validate_repair_plan(plan)
        self.assertEqual(plan["execution_status"], "not_executed")
        self.assertTrue(plan["requires_human_approval"])
        self.assertEqual(plan["risk_level"], "high")
        self.assertTrue(plan["verification_steps"])
        self.assertTrue(plan["rollback_conditions"])

    def test_repair_contract_rejects_execution_claims(self) -> None:
        plan = default_repair_plan(
            {
                "diagnosis_result": {
                    "summary": "test",
                    "hypotheses": [],
                    "evidence_refs": [],
                    "remaining_uncertainties": [],
                }
            }
        )
        plan["steps"] = ["已自动执行接口重启"]

        with self.assertRaisesRegex(ValueError, "must not claim execution"):
            validate_repair_plan(plan)


if __name__ == "__main__":
    unittest.main()
