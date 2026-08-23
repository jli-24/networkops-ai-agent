"""Simulator backend protocol shared by Wokwi/Renode adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from network_agent_rag.packs.embeddedops.domain.models import (
    Framework,
    HardwareDesign,
    SimulationResult,
    SimulationTestCase,
)


@dataclass(frozen=True)
class CompileResult:
    success: bool
    log: tuple[str, ...]
    errors: tuple[str, ...] = ()
    binary_sha256: str | None = None


@dataclass(frozen=True)
class VirtualDevice:
    device_id: str
    mcu_model: str
    peripherals: tuple[str, ...] = field(default=())


@dataclass
class _DeviceRuntime:
    """Mutable per-device state kept inside the backend (serial log)."""

    serial_log: tuple[str, ...] = ()


class SimulatorBackend(Protocol):
    """Tool-gateway boundary between agents and (virtual) hardware.

    Agents never touch devices directly; they resolve a capability whose
    handler delegates to a backend implementing this protocol.
    """

    name: str

    def compile_firmware(self, source: str, framework: Framework) -> CompileResult:
        """Compile firmware source, returning log and binary hash."""
        ...

    def create_virtual_device(self, design: HardwareDesign) -> VirtualDevice:
        """Provision a virtual device mirroring the hardware design."""
        ...

    def run_simulation(
        self,
        device: VirtualDevice,
        firmware_source: str,
        test_cases: tuple[SimulationTestCase, ...],
    ) -> SimulationResult:
        """Run firmware on the virtual device and evaluate test cases."""
        ...

    def read_serial_log(self, device: VirtualDevice) -> tuple[str, ...]:
        """Return the serial log captured by the latest simulation run."""
        ...


__all__ = ["CompileResult", "SimulatorBackend", "VirtualDevice"]
