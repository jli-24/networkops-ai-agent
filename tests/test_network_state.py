"""Tests for Digital Twin snapshots and state evolution."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from network_agent_rag.digital_twin import create_default_campus_network


class NetworkStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulator = create_default_campus_network()

    def test_default_campus_network_contains_seven_devices_and_six_links(self) -> None:
        state = self.simulator.export_state()

        self.assertEqual(
            [device.device_id for device in state.devices],
            [
                "access-sw-01",
                "access-sw-02",
                "ap-01",
                "core-sw-01",
                "dist-sw-01",
                "edge-rtr-01",
                "server-01",
            ],
        )
        self.assertEqual(len(state.links), 6)
        self.assertEqual(state.active_faults, [])

    def test_updates_device_state_with_replacement_semantics(self) -> None:
        before = self.simulator.export_state().timestamp

        updated = self.simulator.update_device_state(
            "core-sw-01",
            status="degraded",
            cpu_usage=82,
            memory_usage=64,
            temperature=51,
            services=["ospf"],
            metadata={"site": "main-campus"},
        )

        self.assertEqual(updated.status, "degraded")
        self.assertEqual(updated.cpu_usage, 82.0)
        self.assertEqual(updated.services, ["ospf"])
        self.assertEqual(updated.metadata, {"site": "main-campus"})
        self.assertGreater(self.simulator.export_state().timestamp, before)
        updated.services.append("mutated")
        self.assertEqual(
            self.simulator.twin.get_device_state("core-sw-01").services,
            ["ospf"],
        )

    def test_updates_an_undirected_link_state(self) -> None:
        updated = self.simulator.update_link_state(
            "core-sw-01",
            "dist-sw-01",
            bandwidth=5000,
            latency=8.5,
            packet_loss=2.5,
            utilization=91,
            status="degraded",
        )

        self.assertEqual(updated.bandwidth, 5000.0)
        self.assertEqual(updated.packet_loss, 2.5)
        self.assertEqual(updated.status, "degraded")
        self.assertEqual(
            self.simulator.twin.get_link_state("dist-sw-01", "core-sw-01"),
            updated,
        )

    def test_invalid_update_is_atomic(self) -> None:
        before = self.simulator.twin.get_device_state("core-sw-01")
        timestamp = self.simulator.export_state().timestamp

        with self.assertRaises(ValidationError):
            self.simulator.update_device_state(
                "core-sw-01",
                status="degraded",
                cpu_usage=101,
            )

        self.assertEqual(self.simulator.twin.get_device_state("core-sw-01"), before)
        self.assertEqual(self.simulator.export_state().timestamp, timestamp)

    def test_identity_fields_cannot_be_updated(self) -> None:
        with self.assertRaises(TypeError):
            self.simulator.update_device_state(
                "core-sw-01",
                hostname="renamed",  # type: ignore[call-arg]
            )
        with self.assertRaises(TypeError):
            self.simulator.update_link_state(
                "core-sw-01",
                "dist-sw-01",
                source="renamed",  # type: ignore[call-arg]
            )

    def test_noop_update_preserves_timestamp_and_returns_a_copy(self) -> None:
        before = self.simulator.export_state().timestamp

        device = self.simulator.update_device_state("core-sw-01")
        link = self.simulator.update_link_state("core-sw-01", "dist-sw-01")

        self.assertEqual(self.simulator.export_state().timestamp, before)
        device.metadata["mutated"] = True
        link.utilization = 99
        self.assertEqual(
            self.simulator.twin.get_device_state("core-sw-01").metadata,
            {"layer": "core"},
        )
        self.assertEqual(
            self.simulator.twin.get_link_state("core-sw-01", "dist-sw-01").utilization,
            0.0,
        )

    def test_state_changes_do_not_infer_active_faults(self) -> None:
        self.simulator.update_device_state("core-sw-01", status="offline")
        self.simulator.update_link_state(
            "core-sw-01",
            "dist-sw-01",
            status="down",
        )

        self.assertEqual(self.simulator.export_state().active_faults, [])


if __name__ == "__main__":
    unittest.main()
