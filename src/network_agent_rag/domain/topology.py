"""Network topology knowledge graph backed by NetworkX."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json

import networkx as nx


_DEVICE_TYPES = {"router", "server", "switch"}
_RELATION_TYPES = {"connect", "routing", "vlan"}
_BIDIRECTIONAL_RELATIONS = {"connect", "vlan"}


class NetworkTopology:
    """Validated network topology with deterministic query helpers."""

    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph

    @classmethod
    def from_json(cls, path: str | Path) -> NetworkTopology:
        """Load devices and relationships from a JSON file."""

        source = Path(path).expanduser().resolve()
        with source.open(encoding="utf-8-sig") as stream:
            payload = json.load(stream)

        root = _require_object(payload, "root")
        devices = _require_list(root, "devices")
        relationships = _require_list(root, "relationships")
        graph = nx.MultiDiGraph()
        for index, raw_device in enumerate(devices):
            location = f"devices[{index}]"
            device = _require_object(raw_device, location)
            device_id = _require_string(device, "id", location)
            device_type = _require_string(device, "type", location)
            if device_type not in _DEVICE_TYPES:
                raise ValueError(
                    f"{location}.type must be one of {sorted(_DEVICE_TYPES)}"
                )
            if device_id in graph:
                raise ValueError(f"Duplicate device id: {device_id}")
            attributes = dict(device)
            attributes.pop("id")
            graph.add_node(device_id, **attributes)

        for index, raw_relationship in enumerate(relationships):
            location = f"relationships[{index}]"
            relationship = _require_object(raw_relationship, location)
            source_id = _require_string(relationship, "source", location)
            target_id = _require_string(relationship, "target", location)
            relation_type = _require_string(relationship, "type", location)
            if relation_type not in _RELATION_TYPES:
                raise ValueError(
                    f"{location}.type must be one of {sorted(_RELATION_TYPES)}"
                )
            missing = [node for node in (source_id, target_id) if node not in graph]
            if missing:
                raise ValueError(
                    f"{location} references unknown device: {missing[0]}"
                )

            attributes = dict(relationship)
            attributes.pop("source")
            attributes.pop("target")
            _add_edge(graph, source_id, target_id, attributes)
            if relation_type in _BIDIRECTIONAL_RELATIONS and source_id != target_id:
                _add_edge(
                    graph,
                    target_id,
                    source_id,
                    _reverse_link_attributes(attributes),
                )

        return cls(graph)

    def query_path(
        self,
        source: str,
        target: str,
        relation: str | None = None,
    ) -> list[str]:
        """Return the shortest directed path, optionally filtered by relation."""

        self._require_device(source)
        self._require_device(target)
        self._validate_relation(relation)
        graph = self._relation_view(relation)
        try:
            return nx.shortest_path(graph, source=source, target=target)
        except nx.NetworkXNoPath:
            return []

    def query_path_details(
        self,
        source: str,
        target: str,
        relation: str | None = None,
    ) -> dict[str, list[Any]]:
        """Return a shortest path with deterministic per-hop link metadata."""

        path = self.query_path(source, target, relation)
        links: list[dict[str, Any]] = []
        for left, right in zip(path, path[1:]):
            candidates = self.graph.get_edge_data(left, right, default={})
            matching = [
                data
                for _, data in sorted(candidates.items(), key=lambda item: str(item[0]))
                if relation is None or data["type"] == relation
            ]
            links.append(
                {
                    "source": left,
                    "target": right,
                    **deepcopy(matching[0]),
                }
            )
        return {"path": path, "links": links}

    def get_neighbors(self, device_id: str, relation: str | None = None) -> list[str]:
        """Return sorted adjacent device ids from incoming and outgoing edges."""

        self._require_device(device_id)
        self._validate_relation(relation)
        neighbors: set[str] = set()
        for source, target, data in self.graph.in_edges(device_id, data=True):
            if relation is None or data["type"] == relation:
                neighbors.add(source)
        for source, target, data in self.graph.out_edges(device_id, data=True):
            if relation is None or data["type"] == relation:
                neighbors.add(target)
        return sorted(neighbors)

    def get_device_info(self, device_id: str) -> dict[str, Any]:
        """Return a copy of one device's attributes."""

        self._require_device(device_id)
        return deepcopy({"id": device_id, **dict(self.graph.nodes[device_id])})

    def _relation_view(self, relation: str | None) -> nx.MultiDiGraph:
        if relation is None:
            return self.graph
        return nx.subgraph_view(
            self.graph,
            filter_edge=lambda source, target, key: self.graph.edges[
                source, target, key
            ]["type"]
            == relation,
        )

    def _require_device(self, device_id: str) -> None:
        if device_id not in self.graph:
            raise KeyError(f"Unknown device: {device_id}")

    @staticmethod
    def _validate_relation(relation: str | None) -> None:
        if relation is not None and relation not in _RELATION_TYPES:
            raise ValueError(f"relation must be one of {sorted(_RELATION_TYPES)}")


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _require_list(payload: dict[str, Any], field: str) -> list[Any]:
    if field not in payload:
        raise ValueError(f"{field} is required")
    value = payload[field]
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _require_string(payload: dict[str, Any], field: str, location: str) -> str:
    if field not in payload:
        raise ValueError(f"{location}.{field} is required")
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{field} must be a non-empty string")
    return value


def _add_edge(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
    attributes: dict[str, Any],
) -> None:
    edge_key = graph.add_edge(source, target)
    graph.edges[source, target, edge_key].update(deepcopy(attributes))


def _reverse_link_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    reversed_attributes = deepcopy(attributes)
    source_interface = reversed_attributes.pop("source_interface", None)
    target_interface = reversed_attributes.pop("target_interface", None)
    if target_interface is not None:
        reversed_attributes["source_interface"] = target_interface
    if source_interface is not None:
        reversed_attributes["target_interface"] = source_interface
    return reversed_attributes
