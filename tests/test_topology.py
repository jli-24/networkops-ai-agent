"""Tests for the network topology knowledge graph."""

from __future__ import annotations

from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest

import networkx as nx


def load_topology(payload: object):
    domain = import_module("network_agent_rag.domain")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "topology.json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        return domain.NetworkTopology.from_json(path)


class NetworkTopologyTests(unittest.TestCase):
    def test_loads_json_and_exposes_topology_queries(self) -> None:
        domain = import_module("network_agent_rag.domain")
        topology_type = getattr(domain, "NetworkTopology", None)
        self.assertIsNotNone(topology_type)
        if topology_type is None:
            return

        payload = {
            "devices": [
                {"id": "r1", "type": "router", "name": "Edge Router", "ip": "10.0.0.1"},
                {"id": "s1", "type": "switch", "name": "Core Switch"},
                {"id": "srv1", "type": "server", "name": "DNS Server"},
            ],
            "relationships": [
                {
                    "source": "r1",
                    "target": "s1",
                    "type": "connect",
                    "port": "Gi0/1",
                    "key": "physical-link",
                },
                {"source": "s1", "target": "srv1", "type": "vlan", "vlan_id": 10},
                {"source": "r1", "target": "srv1", "type": "routing", "protocol": "ospf"},
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "topology.json")
            path.write_text(json.dumps(payload), encoding="utf-8")
            topology = topology_type.from_json(path)

        self.assertIsInstance(topology.graph, nx.MultiDiGraph)
        self.assertTrue(topology.graph.has_edge("s1", "r1"))
        self.assertTrue(topology.graph.has_edge("srv1", "s1"))
        reverse_routing = [
            data
            for data in topology.graph.get_edge_data("srv1", "r1", default={}).values()
            if data["type"] == "routing"
        ]
        self.assertEqual(reverse_routing, [])
        connect_edges = topology.graph.get_edge_data("r1", "s1")
        self.assertTrue(any(data["port"] == "Gi0/1" for data in connect_edges.values()))
        self.assertTrue(
            any(data["key"] == "physical-link" for data in connect_edges.values())
        )
        self.assertEqual(topology.query_path("r1", "srv1", relation="routing"), ["r1", "srv1"])
        self.assertEqual(topology.query_path("srv1", "r1", relation="routing"), [])
        self.assertEqual(topology.get_neighbors("r1"), ["s1", "srv1"])
        self.assertEqual(topology.get_neighbors("s1", relation="connect"), ["r1"])
        self.assertEqual(
            topology.get_device_info("r1"),
            {"id": "r1", "type": "router", "name": "Edge Router", "ip": "10.0.0.1"},
        )

    def test_rejects_invalid_json_schema_with_field_locations(self) -> None:
        invalid_payloads = (
            ({}, "devices"),
            ({"devices": {}, "relationships": []}, "devices"),
            ({"devices": [{}], "relationships": []}, "devices[0].id"),
            (
                {
                    "devices": [{"id": "r1", "type": "firewall"}],
                    "relationships": [],
                },
                "devices[0].type",
            ),
            (
                {
                    "devices": [
                        {"id": "r1", "type": "router"},
                        {"id": "r1", "type": "switch"},
                    ],
                    "relationships": [],
                },
                "Duplicate device id",
            ),
            (
                {
                    "devices": [{"id": "r1", "type": "router"}],
                    "relationships": [{"source": "r1", "target": "missing", "type": "connect"}],
                },
                "relationships[0]",
            ),
            (
                {
                    "devices": [{"id": "r1", "type": "router"}],
                    "relationships": [{"source": "r1", "target": "r1"}],
                },
                "relationships[0].type",
            ),
        )

        for payload, message in invalid_payloads:
            with self.subTest(message=message):
                pattern = message.replace("[", r"\[").replace("]", r"\]")
                with self.assertRaisesRegex(ValueError, pattern):
                    load_topology(payload)

    def test_queries_validate_filters_and_return_device_info_copy(self) -> None:
        topology = load_topology(
            {
                "devices": [
                    {
                        "id": "r1",
                        "type": "router",
                        "name": "Router",
                        "metadata": {"site": "dc1"},
                    }
                ],
                "relationships": [],
            }
        )

        with self.assertRaisesRegex(KeyError, "Unknown device"):
            topology.get_neighbors("missing")
        with self.assertRaisesRegex(KeyError, "Unknown device"):
            topology.get_device_info("missing")
        with self.assertRaisesRegex(ValueError, "relation"):
            topology.query_path("r1", "r1", relation="invalid")

        info = topology.get_device_info("r1")
        info["name"] = "Changed"
        info["metadata"]["site"] = "changed"
        self.assertEqual(topology.get_device_info("r1")["name"], "Router")
        self.assertEqual(topology.get_device_info("r1")["metadata"]["site"], "dc1")

    def test_detailed_path_returns_endpoint_interfaces_in_both_directions(self) -> None:
        topology = load_topology(
            {
                "devices": [
                    {"id": "SW1", "type": "switch"},
                    {"id": "SW2", "type": "switch"},
                ],
                "relationships": [
                    {
                        "source": "SW1",
                        "target": "SW2",
                        "type": "connect",
                        "source_interface": "Gi0/1",
                        "target_interface": "Gi0/24",
                    }
                ],
            }
        )

        forward = topology.query_path_details("SW1", "SW2", relation="connect")
        reverse = topology.query_path_details("SW2", "SW1", relation="connect")

        self.assertEqual(forward["path"], ["SW1", "SW2"])
        self.assertEqual(
            forward["links"],
            [
                {
                    "source": "SW1",
                    "target": "SW2",
                    "type": "connect",
                    "source_interface": "Gi0/1",
                    "target_interface": "Gi0/24",
                }
            ],
        )
        self.assertEqual(reverse["path"], ["SW2", "SW1"])
        self.assertEqual(reverse["links"][0]["source_interface"], "Gi0/24")
        self.assertEqual(reverse["links"][0]["target_interface"], "Gi0/1")

    def test_detailed_path_is_deterministic_and_handles_no_path(self) -> None:
        topology = load_topology(
            {
                "devices": [
                    {"id": "SW1", "type": "switch"},
                    {"id": "SW2", "type": "switch"},
                    {"id": "SW3", "type": "switch"},
                ],
                "relationships": [
                    {
                        "source": "SW1",
                        "target": "SW2",
                        "type": "connect",
                        "source_interface": "Gi0/1",
                        "target_interface": "Gi0/24",
                    },
                    {
                        "source": "SW1",
                        "target": "SW2",
                        "type": "connect",
                        "source_interface": "Gi0/2",
                        "target_interface": "Gi0/23",
                    },
                ],
            }
        )

        details = topology.query_path_details("SW1", "SW2", relation="connect")

        self.assertEqual(details["links"][0]["source_interface"], "Gi0/1")
        self.assertEqual(
            topology.query_path_details("SW1", "SW3", relation="connect"),
            {"path": [], "links": []},
        )


if __name__ == "__main__":
    unittest.main()
