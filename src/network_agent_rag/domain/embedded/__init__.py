"""Embedded device domain models."""

from network_agent_rag.domain.embedded.models import (
    BomItem,
    DebugReport,
    FirmwareArtifact,
    Framework,
    HardwareDesign,
    McuSpec,
    Peripheral,
    PeripheralBus,
    SimulationResult,
    SimulationTestCase,
    TestCaseResult,
    ValidationState,
    VerificationErrorCategory,
    VerificationReport,
)
from network_agent_rag.domain.embedded.mcu_catalog import (
    MCU_CATALOG,
    McuRequirements,
    select_mcu,
)
from network_agent_rag.domain.embedded.approval import (
    ApprovalAction,
    ApprovalRequest,
)

__all__ = [
    "ApprovalAction",
    "ApprovalRequest",
    "BomItem",
    "DebugReport",
    "FirmwareArtifact",
    "Framework",
    "HardwareDesign",
    "MCU_CATALOG",
    "McuRequirements",
    "McuSpec",
    "Peripheral",
    "PeripheralBus",
    "SimulationResult",
    "SimulationTestCase",
    "TestCaseResult",
    "ValidationState",
    "VerificationErrorCategory",
    "VerificationReport",
    "select_mcu",
]
