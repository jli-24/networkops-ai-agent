"""Validated device and physical-link models for the Digital Twin."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)


DeviceType = Literal[
    "router",
    "switch",
    "server",
    "firewall",
    "access_point",
    "controller",
]
DeviceStatus = Literal["online", "degraded", "offline", "maintenance"]
LinkStatus = Literal["up", "degraded", "down"]
NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Percentage = Annotated[float, Field(ge=0, le=100)]


class _DigitalTwinModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Device(_DigitalTwinModel):
    """One network device and its current operational state.

    CPU and memory values are percentages; temperature is Celsius.
    """

    device_id: NonEmptyString
    hostname: NonEmptyString
    device_type: DeviceType
    status: DeviceStatus = "online"
    cpu_usage: Percentage = 10.0
    memory_usage: Percentage = 20.0
    temperature: float = 35.0
    services: list[NonEmptyString] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_keys(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if any(not key.strip() for key in value):
            raise ValueError("metadata keys must be non-empty strings")
        return value


class Link(_DigitalTwinModel):
    """One undirected physical link and its current operational state.

    Bandwidth is Mbps, latency is milliseconds, and packet loss and
    utilization are percentages.
    """

    source: NonEmptyString
    target: NonEmptyString
    bandwidth: Annotated[float, Field(gt=0)]
    latency: Annotated[float, Field(ge=0)] = 0.0
    packet_loss: Percentage = 0.0
    utilization: Percentage = 0.0
    status: LinkStatus = "up"

    @model_validator(mode="after")
    def reject_self_link(self) -> Link:
        if self.source == self.target:
            raise ValueError("source and target must identify different devices")
        return self


__all__ = [
    "Device",
    "DeviceStatus",
    "DeviceType",
    "Link",
    "LinkStatus",
]
