"""Deterministic impact aggregation and scoring for propagated faults."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from network_agent_rag.digital_twin.fault import Fault, _parse_service_target
from network_agent_rag.digital_twin.propagation import (
    PropagationResult,
    _canonical_link,
)
from network_agent_rag.digital_twin.state import NetworkState


ImpactLevel = Literal["low", "medium", "high", "critical"]
ImpactScore = Annotated[float, Field(ge=0, le=100)]
_SEVERITY_WEIGHTS = {
    "warning": 0.4,
    "minor": 0.6,
    "major": 0.8,
    "critical": 1.0,
}


class ImpactResult(BaseModel):
    """Affected assets and a normalized zero-to-one-hundred impact score."""

    model_config = ConfigDict(extra="forbid")

    affected_devices: list[str]
    affected_services: list[str]
    impact_score: ImpactScore
    impact_level: ImpactLevel

    @field_validator("affected_devices", "affected_services")
    @classmethod
    def normalize_references(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("affected references must be non-empty strings")
        return sorted({value.strip() for value in values})


class ImpactAnalyzer:
    """Convert a propagation result into service scope and business impact."""

    def __init__(self, state: NetworkState) -> None:
        self._state = state.model_copy(deep=True)
        self._devices = {device.device_id: device for device in self._state.devices}
        if len(self._devices) != len(self._state.devices):
            raise ValueError("Network state contains duplicate device ids")
        self._links = {
            _canonical_link(link.source, link.target) for link in self._state.links
        }
        if len(self._links) != len(self._state.links):
            raise ValueError("Network state contains duplicate links")
        self._services = {
            f"{device.device_id}:{service}"
            for device in self._state.devices
            for service in device.services
        }

    def analyze(
        self,
        fault: Fault,
        propagation: PropagationResult,
    ) -> ImpactResult:
        if fault.fault_id != propagation.fault_id:
            raise ValueError("Fault id does not match propagation result")
        for device_id in propagation.affected_devices:
            if device_id not in self._devices:
                raise KeyError(f"Unknown affected device: {device_id}")
        for link in propagation.affected_links:
            if link not in self._links:
                raise KeyError(f"Unknown affected link: {link}")

        if fault.fault_type == "service_down":
            device_id, service = _parse_service_target(fault.target)
            device = self._devices.get(device_id)
            if device is None:
                raise KeyError(f"Unknown device: {device_id}")
            if service not in device.services:
                raise KeyError(f"Unknown service: {fault.target}")
            affected_services = [fault.target]
        else:
            affected_services = sorted(
                f"{device_id}:{service}"
                for device_id in propagation.affected_devices
                for service in self._devices[device_id].services
            )

        coverage = (
            50 * _ratio(len(propagation.affected_devices), len(self._devices))
            + 30 * _ratio(len(affected_services), len(self._services))
            + 20 * _ratio(len(propagation.affected_links), len(self._links))
        )
        score = round(min(100.0, coverage * _SEVERITY_WEIGHTS[fault.severity]), 2)
        return ImpactResult(
            affected_devices=propagation.affected_devices,
            affected_services=affected_services,
            impact_score=score,
            impact_level=_impact_level(score),
        )


def _ratio(affected: int, total: int) -> float:
    return affected / total if total else 0.0


def _impact_level(score: float) -> ImpactLevel:
    if score < 25:
        return "low"
    if score < 50:
        return "medium"
    if score < 75:
        return "high"
    return "critical"


__all__ = ["ImpactAnalyzer", "ImpactLevel", "ImpactResult"]
