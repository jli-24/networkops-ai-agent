"""Deterministic network-fault fixtures used by the showcase CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from network_agent_rag.digital_twin import (
    Device,
    Fault,
    FaultPropagationEngine,
    Link,
    NetworkDigitalTwin,
    NetworkSimulator,
    PropagationResult,
)


INCIDENT_ID = "INC-DEMO-001"
DEMO_TIME = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class LinkFailureScenario:
    incident_id: str
    description: str
    risk_level: str
    created_at: datetime
    interface_name: str
    alarm: str
    server_reachable: bool
    fault: Fault
    simulator: NetworkSimulator
    faulty_link: Link
    propagation: PropagationResult


def build_link_failure_scenario() -> LinkFailureScenario:
    """Create the fixed SW-01 to SERVER-01 outage in an isolated Digital Twin."""

    twin = NetworkDigitalTwin()
    twin.add_device(
        Device(
            device_id="SW-01",
            hostname="SW-01",
            device_type="switch",
            services=["switching"],
            metadata={"layer": "edge"},
        )
    )
    twin.add_device(
        Device(
            device_id="SERVER-01",
            hostname="SERVER-01",
            device_type="server",
            services=["application"],
            metadata={"layer": "service"},
        )
    )
    twin.add_link(Link(source="SW-01", target="SERVER-01", bandwidth=10_000))
    simulator = NetworkSimulator(twin)
    healthy_state = simulator.export_state()
    fault = Fault(
        fault_id="FAULT-DEMO-001",
        fault_type="link_failure",
        target="SW-01--SERVER-01",
        severity="major",
        description="SW-01 Gi0/1 link to SERVER-01 is down.",
        timestamp=DEMO_TIME,
    )
    propagation = FaultPropagationEngine(
        healthy_state,
        entry_devices=["SW-01"],
    ).analyze(fault)
    faulty_link = simulator.update_link_state(
        "SW-01",
        "SERVER-01",
        status="down",
        packet_loss=100.0,
    )
    return LinkFailureScenario(
        incident_id=INCIDENT_ID,
        description=(
            "Core switch SW-01 to SERVER-01 link failure caused service "
            "unreachability."
        ),
        risk_level="high",
        created_at=DEMO_TIME,
        interface_name="Gi0/1",
        alarm="LINK_DOWN",
        server_reachable=False,
        fault=fault,
        simulator=simulator,
        faulty_link=faulty_link,
        propagation=propagation,
    )


__all__ = [
    "DEMO_TIME",
    "INCIDENT_ID",
    "LinkFailureScenario",
    "build_link_failure_scenario",
]
