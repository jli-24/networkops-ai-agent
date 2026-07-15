"""Tests for read-only gateway-reachability fault propagation."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from network_agent_rag.digital_twin import (
    Device,
    Fault,
    FaultPropagationEngine,
    Link,
    NetworkState,
    create_default_campus_network,
)


class FaultPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = create_default_campus_network().export_state()
        self.engine = FaultPropagationEngine(self.state)

    @staticmethod
    def fault(
        fault_type: str,
        target: str,
        *,
        fault_id: str = "fault-001",
        severity: str = "critical",
    ) -> Fault:
        return Fault(
            fault_id=fault_id,
            fault_type=fault_type,
            target=target,
            severity=severity,
            description="test fault",
            timestamp=datetime.now(timezone.utc),
        )

    def test_device_failure_propagates_to_every_gateway_disconnected_device(self) -> None:
        result = self.engine.analyze(self.fault("device_down", "core-sw-01"))

        self.assertEqual(
            result.affected_devices,
            [
                "access-sw-01",
                "access-sw-02",
                "ap-01",
                "core-sw-01",
                "dist-sw-01",
                "server-01",
            ],
        )
        self.assertEqual(len(result.affected_links), 6)
        self.assertEqual(result.affected_links, sorted(result.affected_links))

    def test_link_failure_accepts_reverse_target_and_marks_downstream_links(self) -> None:
        result = self.engine.analyze(
            self.fault("link_failure", "access-sw-02--dist-sw-01")
        )

        self.assertEqual(result.affected_devices, ["access-sw-02", "ap-01"])
        self.assertEqual(
            result.affected_links,
            ["access-sw-02--ap-01", "access-sw-02--dist-sw-01"],
        )

    def test_service_failure_is_local_and_does_not_affect_links(self) -> None:
        result = self.engine.analyze(self.fault("service_down", "server-01:dns"))

        self.assertEqual(result.affected_devices, ["server-01"])
        self.assertEqual(result.affected_links, [])

    def test_performance_faults_use_the_link_dependency_scope(self) -> None:
        for fault_type in ("high_latency", "packet_loss"):
            with self.subTest(fault_type=fault_type):
                result = self.engine.analyze(
                    self.fault(fault_type, "dist-sw-01--access-sw-02")
                )
                self.assertEqual(result.affected_devices, ["access-sw-02", "ap-01"])

    def test_redundant_path_prevents_false_device_propagation(self) -> None:
        state = self._redundant_state()
        engine = FaultPropagationEngine(state)

        result = engine.analyze(self.fault("link_failure", "edge-01--switch-01"))

        self.assertEqual(result.affected_devices, [])
        self.assertEqual(result.affected_links, ["edge-01--switch-01"])

    def test_validates_unknown_targets_entries_and_inconsistent_topology(self) -> None:
        for fault in (
            self.fault("device_down", "missing"),
            self.fault("service_down", "server-01:missing"),
            self.fault("link_failure", "core-sw-01--missing"),
        ):
            with self.subTest(fault=fault):
                with self.assertRaises(KeyError):
                    self.engine.analyze(fault)

        with self.assertRaises(ValueError):
            FaultPropagationEngine(self.state, entry_devices=[])
        with self.assertRaises(KeyError):
            FaultPropagationEngine(self.state, entry_devices=["missing"])

        no_entry = self.state.model_copy(deep=True)
        for device in no_entry.devices:
            device.metadata = {}
        with self.assertRaises(ValueError):
            FaultPropagationEngine(no_entry)

        inconsistent = NetworkState(
            devices=[
                Device(
                    device_id="edge-01",
                    hostname="EDGE-01",
                    device_type="router",
                    metadata={"layer": "edge"},
                )
            ],
            links=[Link(source="edge-01", target="missing", bandwidth=1000)],
            timestamp=datetime.now(timezone.utc),
        )
        with self.assertRaises(ValueError):
            FaultPropagationEngine(inconsistent)

    def test_analysis_is_repeatable_and_does_not_mutate_input_state(self) -> None:
        before = self.state.model_dump(mode="json")
        fault = self.fault("device_down", "dist-sw-01")

        first = self.engine.analyze(fault)
        second = self.engine.analyze(fault)

        self.assertEqual(first, second)
        self.assertEqual(self.state.model_dump(mode="json"), before)
        self.assertEqual(self.state.active_faults, [])

    @staticmethod
    def _redundant_state() -> NetworkState:
        devices = [
            Device(
                device_id="edge-01",
                hostname="EDGE-01",
                device_type="router",
                metadata={"layer": "edge"},
            ),
            Device(
                device_id="switch-01",
                hostname="SWITCH-01",
                device_type="switch",
            ),
            Device(
                device_id="switch-02",
                hostname="SWITCH-02",
                device_type="switch",
            ),
        ]
        links = [
            Link(source="edge-01", target="switch-01", bandwidth=1000),
            Link(source="edge-01", target="switch-02", bandwidth=1000),
            Link(source="switch-01", target="switch-02", bandwidth=1000),
        ]
        return NetworkState(
            devices=devices,
            links=links,
            timestamp=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
