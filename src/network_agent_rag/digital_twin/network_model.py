"""In-memory NetworkX model for the Network Digital Twin foundation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import networkx as nx

from network_agent_rag.digital_twin.models import Device, Link
from network_agent_rag.digital_twin.state import NetworkState


class NetworkDigitalTwin:
    """Mutable in-memory graph with validated, isolated state objects."""

    def __init__(self) -> None:
        self._graph = nx.Graph()
        self._timestamp = datetime.now(timezone.utc)
        self._active_faults: list[str] = []

    def add_device(self, device: Device) -> None:
        if device.device_id in self._graph:
            raise ValueError(f"Duplicate device: {device.device_id}")
        self._graph.add_node(device.device_id, device=device.model_copy(deep=True))
        self._touch()

    def add_link(self, link: Link) -> None:
        for endpoint in (link.source, link.target):
            self._require_device(endpoint)
        if self._graph.has_edge(link.source, link.target):
            raise ValueError(f"Duplicate link: {link.source} - {link.target}")
        self._graph.add_edge(
            link.source,
            link.target,
            link=link.model_copy(deep=True),
        )
        self._touch()

    def get_device_state(self, device_id: str) -> Device:
        self._require_device(device_id)
        return self._graph.nodes[device_id]["device"].model_copy(deep=True)

    def get_link_state(self, source: str, target: str) -> Link:
        self._require_link(source, target)
        return self._graph.edges[source, target]["link"].model_copy(deep=True)

    def get_neighbors(self, device_id: str) -> list[str]:
        self._require_device(device_id)
        return sorted(self._graph.neighbors(device_id))

    def export_state(self) -> NetworkState:
        return NetworkState(
            devices=[
                data["device"].model_copy(deep=True)
                for _, data in self._graph.nodes(data=True)
            ],
            links=[
                data["link"].model_copy(deep=True)
                for _, _, data in self._graph.edges(data=True)
            ],
            timestamp=self._timestamp,
            active_faults=list(self._active_faults),
        )

    def _replace_device(self, device: Device) -> Device:
        self._require_device(device.device_id)
        current = self._graph.nodes[device.device_id]["device"]
        if current == device:
            return current.model_copy(deep=True)
        self._graph.nodes[device.device_id]["device"] = device.model_copy(deep=True)
        self._touch()
        return device.model_copy(deep=True)

    def _replace_link(self, source: str, target: str, link: Link) -> Link:
        self._require_link(source, target)
        if {source, target} != {link.source, link.target}:
            raise ValueError("Link endpoints cannot be changed")
        current = self._graph.edges[source, target]["link"]
        if current == link:
            return current.model_copy(deep=True)
        self._graph.edges[source, target]["link"] = link.model_copy(deep=True)
        self._touch()
        return link.model_copy(deep=True)

    def _require_device(self, device_id: str) -> None:
        if device_id not in self._graph:
            raise KeyError(f"Unknown device: {device_id}")

    def _require_link(self, source: str, target: str) -> None:
        if not self._graph.has_edge(source, target):
            raise KeyError(f"Unknown link: {source} - {target}")

    def _touch(self) -> None:
        observed = datetime.now(timezone.utc)
        self._timestamp = max(observed, self._timestamp + timedelta(microseconds=1))


__all__ = ["NetworkDigitalTwin"]
