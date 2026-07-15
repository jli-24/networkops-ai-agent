"""Read-only gateway-reachability fault propagation analysis."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

import networkx as nx
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from network_agent_rag.digital_twin.fault import (
    Fault,
    _parse_link_target,
    _parse_service_target,
)
from network_agent_rag.digital_twin.models import Device
from network_agent_rag.digital_twin.state import NetworkState


NonEmptyReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class PropagationResult(BaseModel):
    """Deterministic devices and physical links affected by one fault."""

    model_config = ConfigDict(extra="forbid")

    fault_id: NonEmptyReference
    affected_devices: list[NonEmptyReference]
    affected_links: list[NonEmptyReference]

    @field_validator("affected_devices")
    @classmethod
    def normalize_devices(cls, values: list[str]) -> list[str]:
        return sorted(set(values))

    @field_validator("affected_links")
    @classmethod
    def normalize_links(cls, values: list[str]) -> list[str]:
        return sorted({_canonical_link(*_parse_link_target(value)) for value in values})


class FaultPropagationEngine:
    """Analyze loss of reachability from one or more network entry devices."""

    def __init__(
        self,
        state: NetworkState,
        *,
        entry_devices: Sequence[str] | None = None,
    ) -> None:
        self._state = state.model_copy(deep=True)
        self._devices = self._index_devices(self._state.devices)
        self._graph = self._build_graph(self._state)
        self._entry_devices = self._resolve_entries(entry_devices)
        self._baseline_reachable = self._reachable(self._graph)

    def analyze(self, fault: Fault) -> PropagationResult:
        if fault.fault_type == "service_down":
            device_id, service = _parse_service_target(fault.target)
            device = self._require_device(device_id)
            if service not in device.services:
                raise KeyError(f"Unknown service: {fault.target}")
            return PropagationResult(
                fault_id=fault.fault_id,
                affected_devices=[device_id],
                affected_links=[],
            )

        if fault.fault_type == "device_down":
            device_id = fault.target
            self._require_device(device_id)
            simulated = self._graph.copy()
            simulated.remove_node(device_id)
            affected_devices = (
                self._baseline_reachable - self._reachable(simulated)
            ) | {device_id}
            affected_links = self._links_for_devices(affected_devices)
        else:
            source, target = _parse_link_target(fault.target)
            self._require_link(source, target)
            simulated = self._graph.copy()
            simulated.remove_edge(source, target)
            affected_devices = self._baseline_reachable - self._reachable(simulated)
            affected_links = self._links_for_devices(affected_devices)
            affected_links.add(_canonical_link(source, target))

        return PropagationResult(
            fault_id=fault.fault_id,
            affected_devices=sorted(affected_devices),
            affected_links=sorted(affected_links),
        )

    @staticmethod
    def _index_devices(devices: list[Device]) -> dict[str, Device]:
        indexed = {device.device_id: device for device in devices}
        if len(indexed) != len(devices):
            raise ValueError("Network state contains duplicate device ids")
        return indexed

    @staticmethod
    def _build_graph(state: NetworkState) -> nx.Graph:
        graph = nx.Graph()
        graph.add_nodes_from(device.device_id for device in state.devices)
        for link in state.links:
            missing = [
                endpoint
                for endpoint in (link.source, link.target)
                if endpoint not in graph
            ]
            if missing:
                raise ValueError(f"Link references unknown device: {missing[0]}")
            if graph.has_edge(link.source, link.target):
                raise ValueError(f"Duplicate link: {link.source} - {link.target}")
            graph.add_edge(link.source, link.target)
        return graph

    def _resolve_entries(self, entries: Sequence[str] | None) -> tuple[str, ...]:
        if entries is None:
            resolved = [
                device.device_id
                for device in self._devices.values()
                if device.metadata.get("layer") == "edge"
            ]
        else:
            if isinstance(entries, str):
                raise ValueError("entry_devices must be a sequence of device ids")
            resolved = []
            for entry in entries:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError("entry device ids must be non-empty strings")
                resolved.append(entry.strip())
        if not resolved:
            raise ValueError("At least one entry device is required")
        for entry in resolved:
            self._require_device(entry)
        return tuple(sorted(set(resolved)))

    def _reachable(self, graph: nx.Graph) -> set[str]:
        reachable: set[str] = set()
        for entry in self._entry_devices:
            if entry in graph:
                reachable.update(nx.node_connected_component(graph, entry))
        return reachable

    def _links_for_devices(self, device_ids: set[str]) -> set[str]:
        return {
            _canonical_link(link.source, link.target)
            for link in self._state.links
            if link.source in device_ids or link.target in device_ids
        }

    def _require_device(self, device_id: str) -> Device:
        try:
            return self._devices[device_id]
        except KeyError:
            raise KeyError(f"Unknown device: {device_id}") from None

    def _require_link(self, source: str, target: str) -> None:
        if not self._graph.has_edge(source, target):
            raise KeyError(f"Unknown link: {_canonical_link(source, target)}")


def _canonical_link(source: str, target: str) -> str:
    return "--".join(sorted((source, target)))


__all__ = ["FaultPropagationEngine", "PropagationResult"]
