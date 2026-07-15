"""Validated fault descriptions for Digital Twin analysis."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)


FaultType = Literal[
    "device_down",
    "service_down",
    "link_failure",
    "high_latency",
    "packet_loss",
]
FaultSeverity = Literal["critical", "major", "minor", "warning"]
FaultText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_LINK_FAULTS = {"link_failure", "high_latency", "packet_loss"}


class Fault(BaseModel):
    """One read-only fault-analysis request."""

    model_config = ConfigDict(extra="forbid")

    fault_id: FaultText
    fault_type: FaultType
    target: FaultText
    severity: FaultSeverity
    description: FaultText
    timestamp: AwareDatetime

    @model_validator(mode="after")
    def validate_target_format(self) -> Fault:
        if self.fault_type == "device_down":
            _parse_device_target(self.target)
        elif self.fault_type == "service_down":
            _parse_service_target(self.target)
        elif self.fault_type in _LINK_FAULTS:
            _parse_link_target(self.target)
        return self


def _parse_device_target(target: str) -> str:
    if ":" in target or "--" in target:
        raise ValueError("device target must use the device-id format")
    return target


def _parse_service_target(target: str) -> tuple[str, str]:
    if target.count(":") != 1 or "--" in target:
        raise ValueError("service target must use the device-id:service format")
    device_id, service = target.split(":", 1)
    if (
        not device_id
        or not service
        or device_id != device_id.strip()
        or service != service.strip()
    ):
        raise ValueError("service target must contain a device and service")
    return device_id, service


def _parse_link_target(target: str) -> tuple[str, str]:
    if target.count("--") != 1 or ":" in target or "---" in target:
        raise ValueError("link target must use the source--target format")
    source, destination = target.split("--", 1)
    if (
        not source
        or not destination
        or source != source.strip()
        or destination != destination.strip()
        or source == destination
    ):
        raise ValueError("link target must contain two different devices")
    return source, destination


__all__ = ["Fault", "FaultSeverity", "FaultType"]
