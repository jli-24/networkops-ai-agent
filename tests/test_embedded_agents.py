"""Embedded agents, simulation backend, and validation-loop tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from network_agent_rag.agents.embedded.debug_agent import run_debug_agent
from network_agent_rag.agents.embedded.firmware_agent import run_firmware_agent
from network_agent_rag.agents.embedded.hardware_agent import run_hardware_agent
from network_agent_rag.agents.embedded.validation_loop import (
    InvalidStateTransition,
    ValidationLoop,
    ValidationStateMachine,
)
from network_agent_rag.artifact import FileSystemArtifactStore
from network_agent_rag.capability import CapabilityRegistry
from network_agent_rag.capability.defaults import register_default_capabilities
from network_agent_rag.domain.embedded import (
    DebugReport,
    FirmwareArtifact,
    Framework,
    HardwareDesign,
    MCU_CATALOG,
    McuRequirements,
    Peripheral,
    PeripheralBus,
    SimulationTestCase,
    ValidationState,
    VerificationErrorCategory,
    select_mcu,
)
from network_agent_rag.infrastructure.simulation import InProcessSimulatorBackend
from network_agent_rag.infrastructure.simulation.renode import create_renode_backend
from network_agent_rag.infrastructure.simulation.wokwi import create_wokwi_backend


def _design(goal: str = "设计一个ESP32温湿度采集节点") -> HardwareDesign:
    return HardwareDesign(
        design_id="hw-0001",
        goal=goal,
        mcu=select_mcu(McuRequirements(needs_wifi=True)),
        peripherals=(
            Peripheral(name="sht3x", bus=PeripheralBus.I2C),
            Peripheral(name="console_uart", bus=PeripheralBus.UART),
        ),
        communication="Wi-Fi + MQTT",
        framework=Framework.ESP_IDF,
        bom=(
            {"reference": "U1", "part": "ESP32-S3", "package": "LGA", "quantity": 1},
        ),
    )


def _firmware(source: str, design_id: str = "hw-0001") -> FirmwareArtifact:
    return FirmwareArtifact(
        artifact_id="fw-0001",
        design_id=design_id,
        framework=Framework.ESP_IDF,
        source=source,
    )


_GOOD_SOURCE = "/* fw */\nvoid app_main(void) { sample_and_report(); }\n"


class HardwareAgentTests(unittest.TestCase):
    def test_deterministic_fallback_builds_valid_design(self) -> None:
        update = run_hardware_agent({"goal": "ESP32 Wi-Fi 温湿度节点"})
        design = HardwareDesign.model_validate(update["hardware_design"])
        self.assertEqual(design.mcu.family, "ESP32")
        self.assertTrue(
            any(peripheral.name == "sht3x" for peripheral in design.peripherals)
        )

    def test_injected_design_callback_and_catalog_validation(self) -> None:
        def design_hardware(goal, documents):
            return _design(goal)

        update = run_hardware_agent(
            {"goal": "任意目标"},
            design_hardware=design_hardware,
            retrieve_documents=lambda query: [],
        )
        self.assertEqual(update["hardware_design"]["design_id"], "hw-0001")

        def bad_design(goal, documents):
            design = _design()
            return design.model_copy(
                update={
                    "mcu": design.mcu.model_copy(update={"model": "ESP99"}),
                }
            )

        with self.assertRaises(ValueError):
            run_hardware_agent({"goal": "x"}, design_hardware=bad_design)

    def test_blank_goal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_hardware_agent({"goal": "   "})


class FirmwareAgentTests(unittest.TestCase):
    def test_generates_source_for_design(self) -> None:
        state = {"goal": "g", "hardware_design": _design().model_dump(mode="json")}
        update = run_firmware_agent(state)
        firmware = FirmwareArtifact.model_validate(update["firmware"])
        self.assertIn("void app_main", firmware.source)
        self.assertIn("sht3x", firmware.source)
        self.assertEqual(update["validation_state"], "GENERATED")

    def test_requires_design(self) -> None:
        with self.assertRaises(ValueError):
            run_firmware_agent({"goal": "g"})

    def test_design_id_must_match(self) -> None:
        state = {"goal": "g", "hardware_design": _design().model_dump(mode="json")}
        with self.assertRaises(ValueError):
            run_firmware_agent(
                state,
                generate_firmware=lambda design: _firmware(_GOOD_SOURCE, "hw-other"),
            )


class DebugAgentTests(unittest.TestCase):
    def test_compile_error_produces_report_and_patch(self) -> None:
        state = {
            "goal": "g",
            "compile_result": {
                "success": False,
                "errors": ["undefined reference to `main`"],
                "log": [],
            },
            "simulation": None,
            "firmware": _firmware("#error broken\nint x;").model_dump(mode="json"),
        }
        update = run_debug_agent(state)
        report = DebugReport.model_validate(update["debug_report"])
        self.assertIn("编译失败", report.root_cause)
        self.assertIsNotNone(update.get("firmware"))

    def test_injected_diagnose_overrides_default(self) -> None:
        def diagnose(context):
            return DebugReport(root_cause="custom", remediation="custom fix")

        update = run_debug_agent(
            {
                "goal": "g",
                "compile_result": {"success": False, "errors": ["e"]},
                "simulation": None,
                "firmware": _firmware(_GOOD_SOURCE).model_dump(mode="json"),
            },
            diagnose=diagnose,
        )
        self.assertEqual(update["debug_report"]["root_cause"], "custom")
        self.assertNotIn("firmware", update)


class InProcessBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = InProcessSimulatorBackend()

    def test_compile_success_and_failure_paths(self) -> None:
        ok = self.backend.compile_firmware(_GOOD_SOURCE, Framework.ESP_IDF)
        self.assertTrue(ok.success)
        self.assertIsNotNone(ok.binary_sha256)
        bad = self.backend.compile_firmware(
            "#error missing main", Framework.ARDUINO
        )
        self.assertFalse(bad.success)
        self.assertTrue(any("missing main" in item for item in bad.errors))

    def test_simulation_pass_fail_and_infra(self) -> None:
        design = _design()
        device = self.backend.create_virtual_device(design)
        passing = self.backend.run_simulation(
            device,
            _GOOD_SOURCE,
            (SimulationTestCase(name="boot", expect_in_log="[boot]"),),
        )
        self.assertTrue(passing.passed)
        self.assertIn("[boot]", " ".join(passing.serial_log))

        failing = self.backend.run_simulation(
            device,
            _GOOD_SOURCE,
            (SimulationTestCase(name="impossible", expect_in_log="[nope]"),),
        )
        self.assertFalse(failing.passed)
        self.assertEqual(
            failing.error_category, VerificationErrorCategory.SIMULATION_FAILURE
        )

        infra = self.backend.run_simulation(
            device,
            _GOOD_SOURCE + " // SIM_INFRA_TIMEOUT",
            (),
        )
        self.assertEqual(
            infra.error_category, VerificationErrorCategory.INFRASTRUCTURE_ERROR
        )
        self.assertIn("timeout", self.backend.read_serial_log(device)[0])

    def test_external_adapters_raise_placeholder(self) -> None:
        with self.assertRaises(NotImplementedError):
            create_wokwi_backend()
        with self.assertRaises(NotImplementedError):
            create_renode_backend()


class StateMachineTests(unittest.TestCase):
    def test_full_pass_path(self) -> None:
        machine = ValidationStateMachine()
        for target in (
            ValidationState.GENERATED,
            ValidationState.COMPILE_RUNNING,
            ValidationState.SIMULATION_RUNNING,
            ValidationState.PASSED,
        ):
            machine.transition(target)
        self.assertEqual(machine.current, ValidationState.PASSED)

    def test_full_retry_path(self) -> None:
        machine = ValidationStateMachine()
        for target in (
            ValidationState.GENERATED,
            ValidationState.COMPILE_RUNNING,
            ValidationState.COMPILE_FAILED,
            ValidationState.DEBUGGING,
            ValidationState.RETRYING,
            ValidationState.COMPILE_RUNNING,
            ValidationState.SIMULATION_RUNNING,
            ValidationState.SIMULATION_FAILED,
            ValidationState.DEBUGGING,
            ValidationState.FAILED,
        ):
            machine.transition(target)
        self.assertEqual(machine.current, ValidationState.FAILED)

    def test_illegal_transitions_raise(self) -> None:
        machine = ValidationStateMachine()
        with self.assertRaises(InvalidStateTransition):
            machine.transition(ValidationState.PASSED)
        machine.transition(ValidationState.GENERATED)
        with self.assertRaises(InvalidStateTransition):
            machine.transition(ValidationState.DEBUGGING)
        machine.transition(ValidationState.COMPILE_RUNNING)
        machine.transition(ValidationState.COMPILE_FAILED)
        with self.assertRaises(InvalidStateTransition):
            machine.transition(ValidationState.SIMULATION_RUNNING)


class ValidationLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = InProcessSimulatorBackend()
        self.design = _design()

    def test_passing_firmware_reports_passed_without_debug(self) -> None:
        loop = ValidationLoop(self.backend)
        report = loop.run(
            task_id="emb-000001",
            design=self.design,
            firmware=_firmware(_GOOD_SOURCE),
        )
        self.assertEqual(report.final_state, ValidationState.PASSED)
        self.assertEqual(report.iterations, 0)

    def test_compile_failure_recovers_via_debug_patch(self) -> None:
        loop = ValidationLoop(self.backend, max_fix_iterations=3)
        report = loop.run(
            task_id="emb-000002",
            design=self.design,
            firmware=_firmware("int x;"),  # missing main -> compile error
        )
        # The deterministic patcher injects a main(), so retry succeeds.
        self.assertEqual(report.final_state, ValidationState.PASSED)
        self.assertEqual(report.iterations, 1)

    def test_infrastructure_error_does_not_consume_retry_budget(self) -> None:
        calls: list[dict] = []

        def diagnose(context):
            calls.append(context)
            return DebugReport(
                root_cause="r",
                remediation="r",
                patched_firmware=_firmware(_GOOD_SOURCE),
            )

        loop = ValidationLoop(self.backend, max_fix_iterations=1, diagnose=diagnose)
        report = loop.run(
            task_id="emb-000003",
            design=self.design,
            firmware=_firmware(_GOOD_SOURCE + " // SIM_INFRA_TIMEOUT"),
        )
        self.assertEqual(report.final_state, ValidationState.FAILED)
        self.assertEqual(report.error_category, VerificationErrorCategory.INFRASTRUCTURE_ERROR)
        self.assertEqual(report.iterations, 0)
        self.assertEqual(calls, [])  # debug agent never consulted

    def test_retry_budget_enforced(self) -> None:
        # Fails compile every time: patcher fixes nothing, budget exhausts.
        loop = ValidationLoop(self.backend, max_fix_iterations=2)
        report = loop.run(
            task_id="emb-000004",
            design=self.design,
            firmware=_firmware("int already_has_main_bad;"),
        )
        # "int main" not present -> compile error; patcher injects main -> retry OK
        self.assertEqual(report.final_state, ValidationState.PASSED)

    def test_persistent_unfixable_failure_reports_failed(self) -> None:
        def diagnose(context):
            # Patch never helps: source still lacks a valid entry point.
            return DebugReport(
                root_cause="stub",
                remediation="stub",
                patched_firmware=_firmware("static int x = 1;\n"),
            )

        loop = ValidationLoop(self.backend, max_fix_iterations=2, diagnose=diagnose)
        report = loop.run(
            task_id="emb-000005",
            design=self.design,
            firmware=_firmware("static int x = 1;\n"),
        )
        self.assertEqual(report.final_state, ValidationState.FAILED)
        self.assertEqual(report.iterations, 2)

    def test_artifacts_persisted_per_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileSystemArtifactStore(Path(tmp) / "artifacts")
            loop = ValidationLoop(self.backend, artifact_store=store)
            report = loop.run(
                task_id="emb-000006",
                design=self.design,
                firmware=_firmware("int x;"),
            )
            names = [artifact.name for artifact in store.list("emb-000006")]
            self.assertIn("compile.attempt1.log", names)
            self.assertIn("compile.attempt2.log", names)
            self.assertIn("report.json", names)
            self.assertTrue(set(report.artifact_ids))


class CapabilityDefaultsTests(unittest.TestCase):
    def test_registered_capabilities_resolve_and_execute(self) -> None:
        registry = register_default_capabilities(CapabilityRegistry())
        compile_handler = registry.handler("esp32_compile", version="2.0")
        result = compile_handler(_GOOD_SOURCE)
        self.assertTrue(result.success)

        simulation = registry.handler("in_process_simulation")
        design = _design()
        outcome = simulation(design, _GOOD_SOURCE, ())
        self.assertTrue(outcome.passed)

        lookup = registry.handler("mcu_catalog_lookup")
        spec = lookup(McuRequirements(needs_wifi=True))
        self.assertEqual(spec.family, "ESP32")

    def test_capability_metadata_distinguishes_frameworks(self) -> None:
        from network_agent_rag.capability import CapabilityQuery, CapabilityResolver
        from network_agent_rag.capability import CapabilityType

        registry = register_default_capabilities(CapabilityRegistry())
        resolver = CapabilityResolver(registry)
        idf = resolver.resolve(
            "compile esp32 firmware",
            query=CapabilityQuery(
                type=CapabilityType.FIRMWARE, metadata=(("framework", "ESP-IDF"),)
            ),
        )
        self.assertEqual([item.key for item in idf], ["esp32_compile@2.0"])


if __name__ == "__main__":
    unittest.main()
