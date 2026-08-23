"""Tests for deterministic Digital Twin impact scoring."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from network_agent_rag.packs.networkops.digital_twin import (
    Device,
    Fault,
    FaultPropagationEngine,
    ImpactAnalyzer,
    Link,
    NetworkState,
    PropagationResult,
    create_default_campus_network,
)


class ImpactAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = create_default_campus_network().export_state()
        self.engine = FaultPropagationEngine(self.state)
        self.analyzer = ImpactAnalyzer(self.state)

    @staticmethod
    def fault(
        fault_type: str,
        target: str,
        *,
        severity: str = "critical",
        fault_id: str = "fault-001",
    ) -> Fault:
        return Fault(
            fault_id=fault_id,
            fault_type=fault_type,
            target=target,
            severity=severity,
            description="test fault",
            timestamp=datetime.now(timezone.utc),
        )

    def test_scores_core_failure_from_device_service_and_link_coverage(self) -> None:
        fault = self.fault("device_down", "core-sw-01")
        propagation = self.engine.analyze(fault)

        impact = self.analyzer.analyze(fault, propagation)

        self.assertEqual(impact.affected_devices, propagation.affected_devices)
        self.assertEqual(len(impact.affected_services), 11)
        self.assertEqual(impact.impact_score, 88.24)
        self.assertEqual(impact.impact_level, "critical")

    def test_service_failure_only_counts_the_named_service(self) -> None:
        fault = self.fault("service_down", "server-01:dns")
        propagation = self.engine.analyze(fault)

        impact = self.analyzer.analyze(fault, propagation)

        self.assertEqual(impact.affected_devices, ["server-01"])
        self.assertEqual(impact.affected_services, ["server-01:dns"])
        self.assertEqual(impact.impact_score, 9.45)
        self.assertEqual(impact.impact_level, "low")

    def test_severity_weights_map_to_all_impact_levels(self) -> None:
        expected = {
            "warning": (35.3, "medium"),
            "minor": (52.95, "high"),
            "major": (70.59, "high"),
            "critical": (88.24, "critical"),
        }
        for severity, (score, level) in expected.items():
            with self.subTest(severity=severity):
                fault = self.fault("device_down", "core-sw-01", severity=severity)
                result = self.engine.analyze(fault)
                impact = self.analyzer.analyze(fault, result)
                self.assertEqual(impact.impact_score, score)
                self.assertEqual(impact.impact_level, level)

    def test_empty_service_and_link_denominators_contribute_zero(self) -> None:
        state = NetworkState(
            devices=[
                Device(
                    device_id="edge-01",
                    hostname="EDGE-01",
                    device_type="router",
                    services=[],
                    metadata={"layer": "edge"},
                )
            ],
            links=[],
            timestamp=datetime.now(timezone.utc),
        )
        fault = self.fault("device_down", "edge-01")
        propagation = FaultPropagationEngine(state).analyze(fault)

        impact = ImpactAnalyzer(state).analyze(fault, propagation)

        self.assertEqual(impact.impact_score, 50.0)
        self.assertEqual(impact.impact_level, "high")

    def test_rejects_mismatched_fault_and_unknown_result_references(self) -> None:
        fault = self.fault("device_down", "core-sw-01")
        mismatched = PropagationResult(
            fault_id="other-fault",
            affected_devices=[],
            affected_links=[],
        )
        with self.assertRaises(ValueError):
            self.analyzer.analyze(fault, mismatched)

        unknown_device = PropagationResult(
            fault_id=fault.fault_id,
            affected_devices=["missing"],
            affected_links=[],
        )
        with self.assertRaises(KeyError):
            self.analyzer.analyze(fault, unknown_device)

        unknown_link = PropagationResult(
            fault_id=fault.fault_id,
            affected_devices=[],
            affected_links=["core-sw-01--missing"],
        )
        with self.assertRaises(KeyError):
            self.analyzer.analyze(fault, unknown_link)


if __name__ == "__main__":
    unittest.main()
