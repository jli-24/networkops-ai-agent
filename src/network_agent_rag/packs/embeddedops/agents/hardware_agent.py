"""Hardware design agent: goal -> hardware_design.json."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document

from network_agent_rag.packs.embeddedops.domain import (
    Framework,
    HardwareDesign,
    MCU_CATALOG,
    McuRequirements,
    Peripheral,
    PeripheralBus,
    select_mcu,
)

DesignCallback = Callable[[str, list[Document]], HardwareDesign]
RetrieveCallback = Callable[[str], list[Document]]

_SENSOR_KEYWORDS: tuple[tuple[str, str, PeripheralBus], ...] = (
    ("温湿度", "sht3x", PeripheralBus.I2C),
    ("湿度", "sht3x", PeripheralBus.I2C),
    ("温度", "sht3x", PeripheralBus.I2C),
    ("光照", "bh1750", PeripheralBus.I2C),
    ("距离", "vl53l0x", PeripheralBus.I2C),
    ("电机", "motor_driver", PeripheralBus.GPIO),
)


def run_hardware_agent(
    state: dict[str, Any],
    *,
    design_hardware: DesignCallback | None = None,
    retrieve_documents: RetrieveCallback | None = None,
) -> dict[str, Any]:
    """Produce a validated ``HardwareDesign`` for the goal.

    LLM-backed designs arrive through the ``design_hardware`` callback; the
    deterministic fallback keeps the platform usable (and testable) without
    a model. Either way the selected MCU must exist in the catalog.
    """

    goal = str(state.get("goal", "")).strip()
    if not goal:
        raise ValueError("goal must be a non-empty string")
    documents: list[Document] = (
        retrieve_documents(goal) if retrieve_documents is not None else []
    )
    if design_hardware is not None:
        design = design_hardware(goal, documents)
    else:
        design = _default_design(goal)
    if design.mcu.model not in MCU_CATALOG:
        raise ValueError(f"selected MCU not in catalog: {design.mcu.model}")
    return {
        "hardware_design": design.model_dump(mode="json"),
        "validation_state": "CREATED",
    }


def _default_design(goal: str) -> HardwareDesign:
    lowered = goal.lower()
    requirements = McuRequirements(
        needs_wifi="wifi" in lowered or "wi-fi" in lowered or "云" in goal or "mqtt" in lowered,
        needs_ble="ble" in lowered or "蓝牙" in goal,
        needs_can="can" in lowered or "plc" in lowered,
    )
    mcu = select_mcu(requirements)
    matched: dict[str, PeripheralBus] = {}
    for keyword, name, bus in _SENSOR_KEYWORDS:
        if keyword in goal and name not in matched:
            matched[name] = bus
    peripherals = [
        Peripheral(name=name, bus=bus) for name, bus in matched.items()
    ]
    if not peripherals:
        peripherals = [Peripheral(name="status_led", bus=PeripheralBus.GPIO)]
    peripherals.append(Peripheral(name="console_uart", bus=PeripheralBus.UART))
    framework = (
        Framework.ESP_IDF
        if "Wi-Fi" in mcu.wireless
        else Framework.FREERTOS_BARE
    )
    communication = "Wi-Fi + MQTT" if "Wi-Fi" in mcu.wireless else "UART"
    return HardwareDesign(
        design_id=f"hw-{uuid4().hex[:8]}",
        goal=goal,
        mcu=mcu,
        peripherals=tuple(peripherals),
        communication=communication,
        framework=framework,
        bom=(
            {
                "reference": "U1",
                "part": mcu.model,
                "package": "module",
                "quantity": 1,
            },
            *(
                {
                    "reference": f"P{index}",
                    "part": peripheral.name,
                    "package": "SMD",
                    "quantity": 1,
                }
                for index, peripheral in enumerate(peripherals, start=1)
            ),
        ),
        notes="generated deterministically by the hardware agent fallback",
    )


__all__ = ["run_hardware_agent"]
