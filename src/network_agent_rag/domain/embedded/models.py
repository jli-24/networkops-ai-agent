"""Immutable embedded-domain contracts: designs, firmware, verification."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmbeddedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PeripheralBus(StrEnum):
    SPI = "SPI"
    I2C = "I2C"
    UART = "UART"
    CAN = "CAN"
    GPIO = "GPIO"
    ADC = "ADC"


class Framework(StrEnum):
    ARDUINO = "Arduino"
    ESP_IDF = "ESP-IDF"
    ZEPHYR = "Zephyr"
    FREERTOS_BARE = "FreeRTOS"


class ValidationState(StrEnum):
    """Explicit validation-loop states; transitions are enforced by the
    state machine in ``agents/embedded/validation_loop.py`` and persisted
    into LangGraph checkpoints."""

    CREATED = "CREATED"
    GENERATED = "GENERATED"
    COMPILE_RUNNING = "COMPILE_RUNNING"
    COMPILE_FAILED = "COMPILE_FAILED"
    SIMULATION_RUNNING = "SIMULATION_RUNNING"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    DEBUGGING = "DEBUGGING"
    RETRYING = "RETRYING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class VerificationErrorCategory(StrEnum):
    COMPILE_ERROR = "COMPILE_ERROR"
    SIMULATION_FAILURE = "SIMULATION_FAILURE"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"


class McuSpec(EmbeddedModel):
    family: str = Field(min_length=1)
    model: str = Field(min_length=1)
    core_clock_mhz: int = Field(gt=0)
    ram_kb: int = Field(gt=0)
    flash_kb: int = Field(gt=0)
    wireless: tuple[str, ...] = ()
    interfaces: tuple[PeripheralBus, ...] = ()


class Peripheral(EmbeddedModel):
    name: str = Field(min_length=1)
    bus: PeripheralBus
    description: str = ""


class BomItem(EmbeddedModel):
    reference: str = Field(min_length=1)
    part: str = Field(min_length=1)
    package: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit_price: float = Field(default=0.0, ge=0.0)
    alternate: str | None = None


class HardwareDesign(EmbeddedModel):
    """The ``hardware_design.json`` contract produced by the hardware agent."""

    design_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    mcu: McuSpec
    peripherals: tuple[Peripheral, ...] = Field(min_length=1)
    communication: str = Field(min_length=1)
    framework: Framework
    bom: tuple[BomItem, ...] = Field(min_length=1)
    notes: str = ""

    @field_validator("bom", "peripherals")
    @classmethod
    def reject_duplicates(cls, values: tuple[EmbeddedModel, ...]) -> tuple[EmbeddedModel, ...]:
        identities = [
            getattr(item, "reference", None) or getattr(item, "name", None)
            for item in values
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("entries must not contain duplicates")
        return values


class FirmwareArtifact(EmbeddedModel):
    artifact_id: str = Field(min_length=1)
    design_id: str = Field(min_length=1)
    filename: str = Field(default="main.c", min_length=1)
    language: str = Field(default="C", min_length=1)
    framework: Framework
    source: str = Field(min_length=1)
    entry_point: str | None = None

    @field_validator("source")
    @classmethod
    def reject_empty_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source must not be blank")
        return value


class SimulationTestCase(EmbeddedModel):
    name: str = Field(min_length=1)
    expect_in_log: str | None = None


class TestCaseResult(EmbeddedModel):
    name: str
    passed: bool
    detail: str = ""


class SimulationResult(EmbeddedModel):
    passed: bool
    error_category: VerificationErrorCategory | None = None
    serial_log: tuple[str, ...] = ()
    test_results: tuple[TestCaseResult, ...] = ()
    binary_sha256: str | None = None
    failure_reason: str | None = None


class DebugReport(EmbeddedModel):
    root_cause: str = Field(min_length=1)
    remediation: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    patched_firmware: FirmwareArtifact | None = None


class VerificationReport(EmbeddedModel):
    task_id: str = Field(min_length=1)
    final_state: ValidationState
    iterations: int = Field(ge=0)
    error_category: VerificationErrorCategory | None = None
    root_cause: str | None = None
    summary: str = Field(min_length=1)
    artifact_ids: tuple[str, ...] = ()


__all__ = [
    "BomItem",
    "DebugReport",
    "FirmwareArtifact",
    "Framework",
    "HardwareDesign",
    "McuSpec",
    "Peripheral",
    "PeripheralBus",
    "SimulationResult",
    "SimulationTestCase",
    "TestCaseResult",
    "ValidationState",
    "VerificationErrorCategory",
    "VerificationReport",
]
