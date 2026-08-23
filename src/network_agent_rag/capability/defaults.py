"""Bootstrap default embedded capabilities into a registry.

Each entry binds a versioned capability to the in-process backend by direct
registration. When additional backends arrive (local gcc, docker build,
Wokwi/Renode), register them under new versions or backends -- the registry
interface and agent-side resolution do not change.
"""

from __future__ import annotations

from typing import Any

from network_agent_rag.auth import Permission
from network_agent_rag.capability import (
    Capability,
    CapabilityRegistry,
    CapabilityType,
)
from network_agent_rag.domain.embedded import (
    Framework,
    HardwareDesign,
    SimulationTestCase,
    select_mcu,
)
from network_agent_rag.domain.embedded.mcu_catalog import McuRequirements
from network_agent_rag.infrastructure.simulation import InProcessSimulatorBackend


def register_default_capabilities(
    registry: CapabilityRegistry,
    *,
    backend: InProcessSimulatorBackend | None = None,
) -> CapabilityRegistry:
    backend = backend or InProcessSimulatorBackend()

    registry.register(
        Capability(
            name="esp32_compile",
            version="1.0",
            type=CapabilityType.FIRMWARE,
            input_schema={"source": "string"},
            output_schema={"binary_sha256": "string", "log": "string[]"},
            permission=Permission.EMBEDDED_SIMULATE,
            backend=backend.name,
            metadata={"framework": "Arduino", "language": "C", "mcu": ["ESP32"]},
        ),
        handler=lambda source: backend.compile_firmware(source, Framework.ARDUINO),
    )
    registry.register(
        Capability(
            name="esp32_compile",
            version="2.0",
            type=CapabilityType.FIRMWARE,
            input_schema={"source": "string"},
            output_schema={"binary_sha256": "string", "log": "string[]"},
            permission=Permission.EMBEDDED_SIMULATE,
            backend=backend.name,
            metadata={"framework": "ESP-IDF", "language": "C", "mcu": ["ESP32-S3"]},
        ),
        handler=lambda source: backend.compile_firmware(source, Framework.ESP_IDF),
    )
    registry.register(
        Capability(
            name="in_process_simulation",
            version="1.0",
            type=CapabilityType.SIMULATION,
            input_schema={"design": "HardwareDesign", "source": "string"},
            output_schema={"result": "SimulationResult"},
            permission=Permission.EMBEDDED_SIMULATE,
            backend=backend.name,
            metadata={"simulator": "in_process", "mcu": ["ESP32", "ESP32-S3", "STM32"]},
        ),
        handler=_simulation_handler(backend),
    )
    registry.register(
        Capability(
            name="mcu_catalog_lookup",
            version="1.0",
            type=CapabilityType.KNOWLEDGE,
            input_schema={"requirements": "McuRequirements"},
            output_schema={"spec": "McuSpec"},
            permission=Permission.EMBEDDED_READ,
            backend=backend.name,
            metadata={"domain": "mcu"},
        ),
        handler=lambda requirements: select_mcu(requirements),
    )
    return registry


def _simulation_handler(
    backend: InProcessSimulatorBackend,
) -> Any:
    def run_simulation(
        design: HardwareDesign,
        source: str,
        test_cases: tuple[SimulationTestCase, ...] = (),
    ):
        device = backend.create_virtual_device(design)
        return backend.run_simulation(device, source, test_cases)

    return run_simulation


__all__ = ["register_default_capabilities"]
