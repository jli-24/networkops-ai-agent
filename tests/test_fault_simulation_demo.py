"""End-to-end contract for the repeatable network-fault demo."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.evaluation import BenchmarkResultStore
from network_agent_rag.observability import SQLiteTraceStore
from network_agent_rag.observability.governance import GovernanceQuery

from demo.fault_scenarios import INCIDENT_ID, build_link_failure_scenario
from demo.run_demo import DemoResult, run_demo


class FaultScenarioTests(unittest.TestCase):
    def test_fixed_link_failure_evidence_and_propagation(self) -> None:
        scenario = build_link_failure_scenario()

        self.assertEqual(scenario.incident_id, INCIDENT_ID)
        self.assertEqual(scenario.risk_level, "high")
        self.assertEqual(scenario.fault.target, "SW-01--SERVER-01")
        self.assertEqual(scenario.interface_name, "Gi0/1")
        self.assertEqual(scenario.alarm, "LINK_DOWN")
        self.assertFalse(scenario.server_reachable)
        self.assertEqual(scenario.faulty_link.status, "down")
        self.assertEqual(scenario.faulty_link.packet_loss, 100.0)
        self.assertEqual(scenario.propagation.affected_devices, ["SERVER-01"])


class FaultSimulationDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = TemporaryDirectory()
        cls.output = StringIO()
        cls.result: DemoResult = run_demo(
            base_directory=Path(cls.temporary.name),
            output=cls.output,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_demo_completes_full_incident_loop(self) -> None:
        result = self.result

        self.assertEqual(result.incident.incident_id, INCIDENT_ID)
        self.assertEqual(result.incident.status, "succeeded")
        self.assertEqual(result.incident.risk, "high")
        self.assertEqual(result.diagnosis, "Interface failure")
        self.assertEqual(result.root_cause, "SW-01 uplink failure")
        self.assertEqual(result.repair_plan, "Enable interface Gi0/1")
        self.assertEqual(result.policy_decision, "REQUIRE_APPROVAL")
        self.assertEqual(result.approval, "APPROVED")
        self.assertEqual(result.execution, "SUCCESS")
        self.assertEqual(result.verification, "PASSED")

        rendered = self.output.getvalue()
        for text in (
            "NetworkOps AI Agent Demo",
            INCIDENT_ID,
            "SW-01 Gi0/1 interface down",
            "Root Cause:",
            "SW-01 uplink failure",
            "REQUIRE_APPROVAL",
            "Demo Completed",
        ):
            self.assertIn(text, rendered)

        trace = SQLiteTraceStore(result.paths.trace_db)
        before = trace.list_spans(INCIDENT_ID)
        repeated = run_demo(
            base_directory=Path(self.temporary.name),
            output=StringIO(),
        )
        after = trace.list_spans(INCIDENT_ID)
        workflows = [span for span in after if span.kind.value == "workflow"]
        latest_duration = round(
            sum(span.duration_ms or 0.0 for span in workflows[-2:]),
            3,
        )

        self.assertEqual(repeated.execution, "SUCCESS")
        self.assertGreater(len(after), len(before))
        self.assertEqual(len({span.run_id for span in workflows}), len(workflows))
        self.assertEqual(workflows[-1].status.value, "succeeded")
        self.assertEqual(repeated.evaluation_time_ms, latest_duration)

    def test_trace_contains_required_demo_nodes(self) -> None:
        spans = SQLiteTraceStore(self.result.paths.trace_db).list_spans(INCIDENT_ID)
        names = {span.name for span in spans}

        self.assertTrue(
            {"Supervisor", "DiagnosisAgent", "search_knowledge", "RepairAgent", "PolicyEngine"}
            <= names
        )
        self.assertTrue(all(span.duration_ms is not None for span in spans))

    def test_governance_and_policy_facts_are_persisted(self) -> None:
        audit = SQLiteAuditLog(self.result.paths.audit_db)
        trace = SQLiteTraceStore(self.result.paths.trace_db)
        events = audit.list_events(INCIDENT_ID)
        governance = GovernanceQuery(audit, trace).query(incident_id=INCIDENT_ID)

        self.assertIn("policy_approval_required", {event.action for event in events})
        self.assertIn("authorize_execution", {event.action for event in events})
        self.assertEqual(self.result.repair_operation, "restart_interface")
        self.assertEqual(self.result.policy_operation, "restart_device")
        policy = next(event for event in events if event.action == "policy_evaluation")
        self.assertEqual(policy.details["operation"], self.result.policy_operation)
        self.assertTrue(
            any(
                event.permission.value == "EXECUTE_REPAIR"
                and event.decision.value == "allowed"
                and event.source.value == "workflow"
                for event in governance
            )
        )

    def test_evaluation_result_is_console_readable(self) -> None:
        runs = BenchmarkResultStore(self.result.paths.evaluation_dir).list_runs()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].cases[0].case_id, "NET-DEMO-001")
        self.assertTrue(runs[0].cases[0].route_correct)
        self.assertEqual(runs[0].cases[0].evidence_recall, 1.0)
        self.assertTrue(runs[0].cases[0].root_cause_correct)
        self.assertTrue(runs[0].cases[0].safe_execution)
        self.assertEqual(self.result.evaluation_case_id, "NET-DEMO-001")
        self.assertEqual(self.result.top1_root_cause, "SW-01 uplink failure")
        self.assertGreaterEqual(self.result.evaluation_time_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
