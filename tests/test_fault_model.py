"""Contract tests for Digital Twin fault models."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from network_agent_rag.packs.networkops.digital_twin import Fault


class FaultModelTests(unittest.TestCase):
    def test_accepts_all_fault_types_and_normalizes_text(self) -> None:
        targets = {
            "device_down": "core-sw-01",
            "service_down": "server-01:dns",
            "link_failure": "core-sw-01--dist-sw-01",
            "high_latency": "core-sw-01--dist-sw-01",
            "packet_loss": "core-sw-01--dist-sw-01",
        }

        for fault_type, target in targets.items():
            with self.subTest(fault_type=fault_type):
                fault = Fault(
                    fault_id=" fault-001 ",
                    fault_type=fault_type,
                    target=f" {target} ",
                    severity="major",
                    description=" test fault ",
                    timestamp=datetime.now(timezone.utc),
                )
                self.assertEqual(fault.fault_id, "fault-001")
                self.assertEqual(fault.target, target)
                self.assertEqual(fault.description, "test fault")

    def test_rejects_targets_that_do_not_match_fault_type(self) -> None:
        invalid_targets = [
            ("device_down", "server-01:dns"),
            ("device_down", "core-sw-01--dist-sw-01"),
            ("service_down", "server-01"),
            ("service_down", "server-01:"),
            ("service_down", ":dns"),
            ("service_down", "server-01: dns"),
            ("link_failure", "core-sw-01"),
            ("link_failure", "core-sw-01-- dist-sw-01"),
            ("link_failure", "core-sw-01---dist-sw-01"),
            ("high_latency", "core-sw-01--"),
            ("packet_loss", "core-sw-01--core-sw-01"),
        ]

        for fault_type, target in invalid_targets:
            with self.subTest(fault_type=fault_type, target=target):
                with self.assertRaises(ValidationError):
                    Fault(
                        fault_id="fault-001",
                        fault_type=fault_type,
                        target=target,
                        severity="major",
                        description="test",
                        timestamp=datetime.now(timezone.utc),
                    )

    def test_rejects_invalid_fields_naive_time_and_extra_data(self) -> None:
        valid = {
            "fault_id": "fault-001",
            "fault_type": "device_down",
            "target": "core-sw-01",
            "severity": "critical",
            "description": "test",
            "timestamp": datetime.now(timezone.utc),
        }
        invalid_payloads = [
            {**valid, "fault_id": " "},
            {**valid, "description": ""},
            {**valid, "fault_type": "unknown"},
            {**valid, "severity": "high"},
            {**valid, "timestamp": datetime.now()},
            {**valid, "unexpected": True},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    Fault.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
