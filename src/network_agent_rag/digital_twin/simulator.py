"""State evolution helpers and a deterministic campus network model."""

from __future__ import annotations

from pydantic import JsonValue

from network_agent_rag.digital_twin.models import (
    Device,
    DeviceStatus,
    Link,
    LinkStatus,
)
from network_agent_rag.digital_twin.network_model import NetworkDigitalTwin
from network_agent_rag.digital_twin.state import NetworkState


class NetworkSimulator:
    """Apply validated state changes to an in-memory Digital Twin."""

    def __init__(self, twin: NetworkDigitalTwin) -> None:
        self.twin = twin

    def update_device_state(
        self,
        device_id: str,
        *,
        status: DeviceStatus | None = None,
        cpu_usage: float | None = None,
        memory_usage: float | None = None,
        temperature: float | None = None,
        services: list[str] | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> Device:
        current = self.twin.get_device_state(device_id)
        updates = {
            name: value
            for name, value in {
                "status": status,
                "cpu_usage": cpu_usage,
                "memory_usage": memory_usage,
                "temperature": temperature,
                "services": services,
                "metadata": metadata,
            }.items()
            if value is not None
        }
        if not updates:
            return current
        updated = Device.model_validate({**current.model_dump(), **updates})
        return self.twin._replace_device(updated)

    def update_link_state(
        self,
        source: str,
        target: str,
        *,
        bandwidth: float | None = None,
        latency: float | None = None,
        packet_loss: float | None = None,
        utilization: float | None = None,
        status: LinkStatus | None = None,
    ) -> Link:
        current = self.twin.get_link_state(source, target)
        updates = {
            name: value
            for name, value in {
                "bandwidth": bandwidth,
                "latency": latency,
                "packet_loss": packet_loss,
                "utilization": utilization,
                "status": status,
            }.items()
            if value is not None
        }
        if not updates:
            return current
        updated = Link.model_validate({**current.model_dump(), **updates})
        return self.twin._replace_link(source, target, updated)

    def export_state(self) -> NetworkState:
        return self.twin.export_state()


def create_default_campus_network() -> NetworkSimulator:
    """Build the healthy seven-device campus topology used by the preview."""

    twin = NetworkDigitalTwin()
    devices = [
        Device(
            device_id="edge-rtr-01",
            hostname="EDGE-RTR-01",
            device_type="router",
            services=["ospf", "nat"],
            metadata={"layer": "edge"},
        ),
        Device(
            device_id="core-sw-01",
            hostname="CORE-SW-01",
            device_type="switch",
            services=["ospf", "stp"],
            metadata={"layer": "core"},
        ),
        Device(
            device_id="dist-sw-01",
            hostname="DIST-SW-01",
            device_type="switch",
            services=["stp", "vlan"],
            metadata={"layer": "distribution"},
        ),
        Device(
            device_id="access-sw-01",
            hostname="ACCESS-SW-01",
            device_type="switch",
            services=["stp", "vlan"],
            metadata={"layer": "access"},
        ),
        Device(
            device_id="access-sw-02",
            hostname="ACCESS-SW-02",
            device_type="switch",
            services=["stp", "vlan"],
            metadata={"layer": "access"},
        ),
        Device(
            device_id="server-01",
            hostname="SERVER-01",
            device_type="server",
            services=["dns", "dhcp"],
            metadata={"layer": "service"},
        ),
        Device(
            device_id="ap-01",
            hostname="AP-01",
            device_type="access_point",
            services=["wireless"],
            metadata={"layer": "access"},
        ),
    ]
    for device in devices:
        twin.add_device(device)

    for source, target, bandwidth in [
        ("edge-rtr-01", "core-sw-01", 10_000),
        ("core-sw-01", "dist-sw-01", 10_000),
        ("core-sw-01", "server-01", 10_000),
        ("dist-sw-01", "access-sw-01", 1_000),
        ("dist-sw-01", "access-sw-02", 1_000),
        ("access-sw-02", "ap-01", 1_000),
    ]:
        twin.add_link(Link(source=source, target=target, bandwidth=bandwidth))

    return NetworkSimulator(twin)


__all__ = ["NetworkSimulator", "create_default_campus_network"]
