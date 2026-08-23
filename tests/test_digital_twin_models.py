"""Contract tests for Digital Twin Pydantic models."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from network_agent_rag.packs.networkops.digital_twin import Device, Link


class DigitalTwinModelTests(unittest.TestCase):
    def test_device_uses_healthy_defaults_and_serializes_units(self) -> None:
        device = Device(
            device_id="core-sw-01",
            hostname="CORE-SW-01",
            device_type="switch",
        )

        self.assertEqual(device.status, "online")
        self.assertEqual(device.cpu_usage, 10.0)
        self.assertEqual(device.memory_usage, 20.0)
        self.assertEqual(device.temperature, 35.0)
        self.assertEqual(device.services, [])
        self.assertEqual(device.metadata, {})
        self.assertEqual(device.model_dump()["device_type"], "switch")

    def test_device_rejects_invalid_identifiers_values_and_extra_fields(self) -> None:
        valid = {
            "device_id": "core-sw-01",
            "hostname": "CORE-SW-01",
            "device_type": "switch",
        }

        invalid_payloads = [
            {**valid, "device_id": " "},
            {**valid, "hostname": ""},
            {**valid, "device_type": "printer"},
            {**valid, "status": "unknown"},
            {**valid, "cpu_usage": -0.1},
            {**valid, "memory_usage": 100.1},
            {**valid, "unexpected": True},
            {**valid, "metadata": {"not_json": object()}},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    Device.model_validate(payload)

    def test_link_uses_healthy_defaults_and_explicit_units(self) -> None:
        link = Link(source="core-sw-01", target="server-01", bandwidth=10_000)

        self.assertEqual(link.bandwidth, 10_000.0)
        self.assertEqual(link.latency, 0.0)
        self.assertEqual(link.packet_loss, 0.0)
        self.assertEqual(link.utilization, 0.0)
        self.assertEqual(link.status, "up")

    def test_link_rejects_self_links_invalid_ranges_and_extra_fields(self) -> None:
        valid = {
            "source": "core-sw-01",
            "target": "server-01",
            "bandwidth": 10_000,
        }

        invalid_payloads = [
            {**valid, "source": " "},
            {**valid, "target": ""},
            {**valid, "target": "core-sw-01"},
            {**valid, "bandwidth": 0},
            {**valid, "latency": -0.1},
            {**valid, "packet_loss": 100.1},
            {**valid, "utilization": -0.1},
            {**valid, "status": "unknown"},
            {**valid, "unexpected": True},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    Link.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
