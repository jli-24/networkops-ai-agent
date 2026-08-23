"""EmbeddedOps pack capabilities and handler binding.

Capability *declarations* are module-level instances consumed by the pack
manifest and registered through the PackRegistry pipeline; handler
*binding* is backend-specific and done by the pack bootstrap after the
pipeline accepts the spec (see ``bind_embeddedops_handlers``). When
additional backends arrive (local gcc, docker build, Wokwi/Renode),
register them under new versions or backends -- the registry interface
and agent-side resolution do not change.
"""

from __future__ import annotations

from typing import Any

from network_agent_rag.auth import Permission
from network_agent_rag.capability import (
    Capability,
    CapabilityRegistry,
    CapabilityType,
)
from network_agent_rag.packs.embeddedops.domain import (
    Framework,
    HardwareDesign,
    SimulationTestCase,
    select_mcu,
)
from network_agent_rag.packs.embeddedops.domain.mcu_catalog import McuRequirements
from network_agent_rag.packs.embeddedops.simulation import InProcessSimulatorBackend


ESP32_COMPILE_ARDUINO = Capability(
    name="esp32_compile",
    version="1.0",
    type=CapabilityType.FIRMWARE,
    input_schema={"source": "string"},
    output_schema={"binary_sha256": "string", "log": "string[]"},
    permission=Permission.EMBEDDED_SIMULATE,
    backend="in_process",
    metadata={"framework": "Arduino", "language": "C", "mcu": ["ESP32"]},
)

ESP32_COMPILE_IDF = Capability(
    name="esp32_compile",
    version="2.0",
    type=CapabilityType.FIRMWARE,
    input_schema={"source": "string"},
    output_schema={"binary_sha256": "string", "log": "string[]"},
    permission=Permission.EMBEDDED_SIMULATE,
    backend="in_process",
    metadata={"framework": "ESP-IDF", "language": "C", "mcu": ["ESP32-S3"]},
)

IN_PROCESS_SIMULATION = Capability(
    name="in_process_simulation",
    version="1.0",
    type=CapabilityType.SIMULATION,
    input_schema={"design": "HardwareDesign", "source": "string"},
    output_schema={"result": "SimulationResult"},
    permission=Permission.EMBEDDED_SIMULATE,
    backend="in_process",
    metadata={"simulator": "in_process", "mcu": ["ESP32", "ESP32-S3", "STM32"]},
)

MCU_CATALOG_LOOKUP = Capability(
    name="mcu_catalog_lookup",
    version="1.0",
    type=CapabilityType.KNOWLEDGE,
    input_schema={"requirements": "McuRequirements"},
    output_schema={"spec": "McuSpec"},
    permission=Permission.EMBEDDED_READ,
    backend="in_process",
    metadata={"domain": "mcu"},
)

EMBEDDEDOPS_CAPABILITIES: tuple[Capability, ...] = (
    ESP32_COMPILE_ARDUINO,
    ESP32_COMPILE_IDF,
    IN_PROCESS_SIMULATION,
    MCU_CATALOG_LOOKUP,
)


def bind_embeddedops_handlers(
    registry: CapabilityRegistry,
    *,
    backend: InProcessSimulatorBackend | None = None,
) -> None:
    """Bind execution handlers onto pipeline-registered capabilities."""

    backend = backend or InProcessSimulatorBackend()
    registry.bind_handler(
        "esp32_compile",
        lambda source: backend.compile_firmware(source, Framework.ARDUINO),
        version="1.0",
    )
    registry.bind_handler(
        "esp32_compile",
        lambda source: backend.compile_firmware(source, Framework.ESP_IDF),
        version="2.0",
    )
    registry.bind_handler(
        "in_process_simulation",
        _simulation_handler(backend),
        version="1.0",
    )
    registry.bind_handler(
        "mcu_catalog_lookup",
        lambda requirements: select_mcu(requirements),
        version="1.0",
    )


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


__all__ = [
    "EMBEDDEDOPS_CAPABILITIES",
    "ESP32_COMPILE_ARDUINO",
    "ESP32_COMPILE_IDF",
    "IN_PROCESS_SIMULATION",
    "MCU_CATALOG_LOOKUP",
    "bind_embeddedops_handlers",
]
