"""Embedded domain models, MCU catalog, task store, and artifact store tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from network_agent_rag.artifact import (
    ArtifactType,
    FileSystemArtifactStore,
    compute_sha256,
)
from network_agent_rag.domain.embedded import (
    ApprovalAction,
    ApprovalRequest,
    Framework,
    HardwareDesign,
    McuSpec,
    MCU_CATALOG,
    McuRequirements,
    Peripheral,
    PeripheralBus,
    ValidationState,
    select_mcu,
)
from network_agent_rag.domain.task import InMemoryTaskStore, Task, TaskDomain, TaskStatus, new_task_id
from network_agent_rag.policy import PolicyRiskLevel


def _design(goal: str = "ESP32 温湿度采集节点") -> HardwareDesign:
    return HardwareDesign(
        design_id="hw-0001",
        goal=goal,
        mcu=MCU_CATALOG["ESP32-S3"],
        peripherals=(Peripheral(name="sht3x", bus=PeripheralBus.I2C),),
        communication="Wi-Fi + MQTT",
        framework=Framework.ESP_IDF,
        bom=(
            {
                "reference": "U1",
                "part": "ESP32-S3-WROOM-1",
                "package": "LGA",
                "quantity": 1,
            },
        ),
    )


class HardwareDesignTests(unittest.TestCase):
    def test_rejects_duplicate_peripheral_names(self) -> None:
        with self.assertRaises(ValueError):
            HardwareDesign(
                **{
                    **_design().model_dump(),
                    "peripherals": [
                        {"name": "sht3x", "bus": "I2C"},
                        {"name": "sht3x", "bus": "I2C"},
                    ],
                }
            )

    def test_validation_states_cover_full_loop(self) -> None:
        states = {state.value for state in ValidationState}
        self.assertIn("COMPILE_FAILED", states)
        self.assertIn("SIMULATION_FAILED", states)
        self.assertIn("RETRYING", states)
        self.assertIn("PASSED", states)


class McuCatalogTests(unittest.TestCase):
    def test_wifi_requirement_selects_esp32(self) -> None:
        spec = select_mcu(McuRequirements(needs_wifi=True))
        self.assertEqual(spec.family, "ESP32")

    def test_can_requirement_selects_stm32(self) -> None:
        spec = select_mcu(McuRequirements(needs_can=True))
        self.assertEqual(spec.family, "STM32")

    def test_ble_requirement_prefers_low_power(self) -> None:
        spec = select_mcu(McuRequirements(needs_ble=True))
        self.assertEqual(spec.model, "nRF52840")

    def test_preferred_model_must_exist(self) -> None:
        with self.assertRaises(ValueError):
            select_mcu(McuRequirements(preferred_model="ESP99"))

    def test_impossible_requirements_raise(self) -> None:
        with self.assertRaises(ValueError):
            select_mcu(McuRequirements(min_ram_kb=9999))


class ApprovalRequestTests(unittest.TestCase):
    def test_execution_plan_is_required_and_non_blank(self) -> None:
        request = ApprovalRequest(
            request_id="appr-0001",
            action=ApprovalAction.FIRMWARE_FLASH,
            target="ESP32-001",
            risk_level=PolicyRiskLevel.HIGH,
            artifact_ref="firmware_v1.2.bin",
            execution_plan=(
                "backup current firmware",
                "flash v1.2",
                "health check",
            ),
        )
        self.assertEqual(len(request.execution_plan), 3)
        with self.assertRaises(ValueError):
            ApprovalRequest(
                **{**request.model_dump(), "execution_plan": [" "]}
            )


class TaskStoreTests(unittest.TestCase):
    def test_task_lifecycle_and_domain_listing(self) -> None:
        store = InMemoryTaskStore()
        task_id = new_task_id(TaskDomain.EMBEDDED, 1)
        task = store.create(
            Task(task_id=task_id, goal="设计并验证", domain=TaskDomain.EMBEDDED)
        )
        self.assertEqual(task.status, TaskStatus.CREATED)

        running = store.update_status(task_id, TaskStatus.RUNNING, workflow_id="w-1")
        self.assertEqual(running.workflow_id, "w-1")
        done = store.update_status(
            task_id,
            TaskStatus.COMPLETED,
            validation_state="PASSED",
            summary="ok",
        )
        self.assertEqual(done.validation_state, "PASSED")
        self.assertEqual(store.list(TaskDomain.EMBEDDED)[0].task_id, task_id)
        self.assertEqual(store.list(TaskDomain.NETWORK), [])

    def test_unknown_task_update_raises(self) -> None:
        store = InMemoryTaskStore()
        with self.assertRaises(KeyError):
            store.update_status("emb-999999", TaskStatus.FAILED)


class ArtifactStoreTests(unittest.TestCase):
    def test_save_lists_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileSystemArtifactStore(Path(tmp) / "artifacts")
            artifact = store.save(
                task_id="emb-000001",
                type=ArtifactType.FIRMWARE_SOURCE,
                name="main.c",
                version="1",
                created_by="FirmwareAgent",
                content="int main(void){return 0;}",
            )
            self.assertEqual(
                artifact.sha256,
                compute_sha256("int main(void){return 0;}"),
            )
            listed = store.list("emb-000001")
            self.assertEqual([item.name for item in listed], ["main.c"])
            self.assertTrue(store.verify_integrity(artifact))
            tampered = artifact.model_copy(update={"sha256": "0" * 64})
            self.assertFalse(store.verify_integrity(tampered))

    def test_duplicate_names_rejected_and_content_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileSystemArtifactStore(Path(tmp) / "artifacts")
            store.save(
                task_id="emb-000002",
                type=ArtifactType.REPORT,
                name="report.json",
                version="1",
                created_by="ValidationLoop",
                content='{"state":"PASSED"}',
            )
            with self.assertRaises(FileExistsError):
                store.save(
                    task_id="emb-000002",
                    type=ArtifactType.REPORT,
                    name="report.json",
                    version="2",
                    created_by="ValidationLoop",
                    content="duplicate",
                )
            content = store.read(store.list("emb-000002")[0])
            self.assertEqual(content, b'{"state":"PASSED"}')

    def test_manifest_survives_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "artifacts"
            FileSystemArtifactStore(root).save(
                task_id="emb-000003",
                type=ArtifactType.COMPILE_LOG,
                name="compile.log",
                version="1",
                created_by="SimulationAgent",
                content="ok",
            )
            reopened = FileSystemArtifactStore(root)
            self.assertEqual(
                [item.name for item in reopened.list("emb-000003")],
                ["compile.log"],
            )


if __name__ == "__main__":
    unittest.main()
