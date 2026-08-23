"""Built-in MCU selection catalog with deterministic selection rules."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from network_agent_rag.packs.embeddedops.domain.models import McuSpec, PeripheralBus


_MCU_CATALOG: tuple[McuSpec, ...] = (
    McuSpec(
        family="ESP32",
        model="ESP32-S3",
        core_clock_mhz=240,
        ram_kb=512,
        flash_kb=16384,
        wireless=("Wi-Fi", "BLE"),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
    McuSpec(
        family="ESP32",
        model="ESP32-WROOM-32",
        core_clock_mhz=240,
        ram_kb=520,
        flash_kb=4096,
        wireless=("Wi-Fi", "BLE"),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
    McuSpec(
        family="STM32",
        model="STM32F103C8T6",
        core_clock_mhz=72,
        ram_kb=20,
        flash_kb=64,
        wireless=(),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.CAN,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
    McuSpec(
        family="STM32",
        model="STM32F407VGT6",
        core_clock_mhz=168,
        ram_kb=192,
        flash_kb=1024,
        wireless=(),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.CAN,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
    McuSpec(
        family="Nordic",
        model="nRF52840",
        core_clock_mhz=64,
        ram_kb=256,
        flash_kb=1024,
        wireless=("BLE",),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
    McuSpec(
        family="RaspberryPi",
        model="RP2040",
        core_clock_mhz=133,
        ram_kb=264,
        flash_kb=2048,
        wireless=(),
        interfaces=(
            PeripheralBus.SPI,
            PeripheralBus.I2C,
            PeripheralBus.UART,
            PeripheralBus.ADC,
            PeripheralBus.GPIO,
        ),
    ),
)

MCU_CATALOG: dict[str, McuSpec] = {spec.model: spec for spec in _MCU_CATALOG}


class McuRequirements(BaseModel):
    """Declarative requirements consumed by the deterministic selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    needs_wifi: bool = False
    needs_ble: bool = False
    needs_can: bool = False
    min_ram_kb: int = Field(default=0, ge=0)
    min_clock_mhz: int = Field(default=0, ge=0)
    preferred_model: str | None = None


def select_mcu(requirements: McuRequirements) -> McuSpec:
    """Pick a catalog MCU by deterministic rules.

    Order matters and is stable: explicit preference, then Wi-Fi, CAN, BLE,
    then the least-powerful catalog entry that satisfies RAM/clock floors.
    """

    if requirements.preferred_model is not None:
        preferred = MCU_CATALOG.get(requirements.preferred_model)
        if preferred is None:
            raise ValueError(
                f"preferred MCU not in catalog: {requirements.preferred_model}"
            )
        return preferred
    if requirements.needs_wifi:
        candidates = [spec for spec in _MCU_CATALOG if "Wi-Fi" in spec.wireless]
    elif requirements.needs_can:
        candidates = [spec for spec in _MCU_CATALOG if PeripheralBus.CAN in spec.interfaces]
    elif requirements.needs_ble:
        candidates = [spec for spec in _MCU_CATALOG if "BLE" in spec.wireless]
    else:
        candidates = list(_MCU_CATALOG)
    suitable = [
        spec
        for spec in candidates
        if spec.ram_kb >= requirements.min_ram_kb
        and spec.core_clock_mhz >= requirements.min_clock_mhz
    ]
    if not suitable:
        raise ValueError("no catalog MCU satisfies the given requirements")
    return min(suitable, key=lambda spec: (spec.ram_kb, spec.core_clock_mhz, spec.model))


__all__ = ["MCU_CATALOG", "McuRequirements", "select_mcu"]
