"""Firmware agent: hardware design -> C source artifact."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from network_agent_rag.domain.embedded import FirmwareArtifact, Framework, HardwareDesign

GenerateCallback = Callable[[HardwareDesign], FirmwareArtifact]


def run_firmware_agent(
    state: dict[str, Any],
    *,
    generate_firmware: GenerateCallback | None = None,
) -> dict[str, Any]:
    """Generate firmware for the state's hardware design."""

    design = _design_from_state(state)
    if generate_firmware is not None:
        artifact = generate_firmware(design)
    else:
        artifact = _default_firmware(design)
    if artifact.design_id != design.design_id:
        raise ValueError("firmware design_id must match the hardware design")
    return {
        "firmware": artifact.model_dump(mode="json"),
        "validation_state": "GENERATED",
    }


def _design_from_state(state: dict[str, Any]) -> HardwareDesign:
    raw = state.get("hardware_design")
    if not isinstance(raw, dict):
        raise ValueError("hardware_design is required before firmware generation")
    return HardwareDesign.model_validate(raw)


def _default_firmware(design: HardwareDesign) -> FirmwareArtifact:
    peripheral_lines = "\n".join(
        f"    peripheral_init(\"{peripheral.name}\", {peripheral.bus.value});"
        for peripheral in design.peripherals
    )
    entry = (
        "void app_main(void)"
        if design.framework == Framework.ESP_IDF
        else "int main(void)"
    )
    returns = "" if design.framework == Framework.ESP_IDF else "    return 0;\n"
    source = (
        f"/* firmware for {design.goal} */\n"
        f"#include \"{design.framework.value.lower()}_hal.h\"\n"
        "\n"
        f"{entry} {{\n"
        f"    system_init();\n"
        f"{peripheral_lines}\n"
        f"    comm_connect(\"{design.communication}\");\n"
        "    for (;;) {\n"
        "        sample_and_report();\n"
        "        sleep_ms(1000);\n"
        "    }\n"
        f"{returns}}}\n"
    )
    return FirmwareArtifact(
        artifact_id=f"fw-{uuid4().hex[:8]}",
        design_id=design.design_id,
        filename="main.c",
        language="C",
        framework=design.framework,
        source=source,
        entry_point=entry,
    )


__all__ = ["run_firmware_agent"]
