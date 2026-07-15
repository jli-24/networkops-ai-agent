"""Tests for the in-memory Network Digital Twin graph."""

from __future__ import annotations

from datetime import timezone
import unittest

from network_agent_rag.digital_twin import Device, Link, NetworkDigitalTwin


class NetworkDigitalTwinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.twin = NetworkDigitalTwin()
        for device_id in ("dist-sw-01", "core-sw-01", "server-01"):
            self.twin.add_device(
                Device(
                    device_id=device_id,
                    hostname=device_id.upper(),
                    device_type="server" if device_id == "server-01" else "switch",
                )
            )

    def test_adds_and_queries_isolated_device_copies(self) -> None:
        device = self.twin.get_device_state("core-sw-01")
        device.metadata["mutated"] = True

        self.assertEqual(self.twin.get_device_state("core-sw-01").metadata, {})
        with self.assertRaises(KeyError):
            self.twin.get_device_state("missing")

    def test_undirected_link_queries_and_neighbors_are_deterministic(self) -> None:
        self.twin.add_link(
            Link(source="core-sw-01", target="server-01", bandwidth=10_000)
        )
        self.twin.add_link(
            Link(source="dist-sw-01", target="core-sw-01", bandwidth=10_000)
        )

        reverse = self.twin.get_link_state("server-01", "core-sw-01")

        self.assertEqual(reverse.source, "core-sw-01")
        self.assertEqual(reverse.target, "server-01")
        self.assertEqual(
            self.twin.get_neighbors("core-sw-01"),
            ["dist-sw-01", "server-01"],
        )

    def test_rejects_duplicate_and_unknown_graph_objects(self) -> None:
        with self.assertRaises(ValueError):
            self.twin.add_device(
                Device(
                    device_id="core-sw-01",
                    hostname="duplicate",
                    device_type="switch",
                )
            )
        with self.assertRaises(KeyError):
            self.twin.add_link(
                Link(source="core-sw-01", target="missing", bandwidth=1000)
            )

        link = Link(source="core-sw-01", target="server-01", bandwidth=10_000)
        self.twin.add_link(link)
        with self.assertRaises(ValueError):
            self.twin.add_link(
                Link(source="server-01", target="core-sw-01", bandwidth=10_000)
            )
        with self.assertRaises(KeyError):
            self.twin.get_link_state("core-sw-01", "dist-sw-01")
        with self.assertRaises(KeyError):
            self.twin.get_neighbors("missing")

    def test_export_state_is_sorted_utc_and_isolated(self) -> None:
        self.twin.add_link(
            Link(source="core-sw-01", target="server-01", bandwidth=10_000)
        )

        snapshot = self.twin.export_state()
        snapshot.devices[0].metadata["mutated"] = True
        snapshot.links[0].utilization = 99

        self.assertEqual(
            [device.device_id for device in snapshot.devices],
            ["core-sw-01", "dist-sw-01", "server-01"],
        )
        self.assertIs(snapshot.timestamp.tzinfo, timezone.utc)
        self.assertEqual(self.twin.get_device_state("core-sw-01").metadata, {})
        self.assertEqual(
            self.twin.get_link_state("core-sw-01", "server-01").utilization,
            0.0,
        )
        self.assertEqual(self.twin.export_state().active_faults, [])


if __name__ == "__main__":
    unittest.main()
