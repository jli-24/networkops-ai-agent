"""Serializable snapshots of a Network Digital Twin."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from network_agent_rag.digital_twin.models import Device, Link


class NetworkState(BaseModel):
    """An isolated, point-in-time view of the modeled network."""

    model_config = ConfigDict(extra="forbid")

    devices: list[Device]
    links: list[Link]
    timestamp: AwareDatetime
    active_faults: list[str] = Field(default_factory=list)

    @field_validator("devices")
    @classmethod
    def sort_devices(cls, devices: list[Device]) -> list[Device]:
        return sorted(devices, key=lambda device: device.device_id)

    @field_validator("links")
    @classmethod
    def sort_links(cls, links: list[Link]) -> list[Link]:
        return sorted(
            links,
            key=lambda link: tuple(sorted((link.source, link.target))),
        )


__all__ = ["NetworkState"]
