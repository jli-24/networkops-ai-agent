"""EmbeddedOps HTTP API tests: task lifecycle, approval, capabilities."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from network_agent_rag.api.embedded import (
    EmbeddedServices,
    create_capabilities_router,
    create_embedded_router,
)


def _client() -> TestClient:
    tmp = tempfile.TemporaryDirectory()
    services = EmbeddedServices(artifact_root=str(Path(tmp.name) / "artifacts"))
    app = FastAPI()
    app.include_router(create_embedded_router(services), prefix="/api/v1")
    app.include_router(create_capabilities_router(services), prefix="/api/v1")
    client = TestClient(app)
    client._tmp = tmp  # type: ignore[attr-defined]
    return client


class EmbeddedTaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def tearDown(self) -> None:
        self.client._tmp.cleanup()  # type: ignore[attr-defined]

    def test_task_lifecycle_awaiting_approval_then_complete(self) -> None:
        created = self.client.post(
            "/api/v1/embedded/tasks", json={"goal": "ESP32 温湿度节点"}
        )
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual(body["status"], "AWAITING_APPROVAL")
        task_id = body["task_id"]

        detail = self.client.get(f"/api/v1/embedded/tasks/{task_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["approval_request"]["action"], "simulation_run")
        self.assertEqual(detail.json()["firmware_filename"], "main.c")
        artifact_names = [item["name"] for item in detail.json()["artifacts"]]
        self.assertIn("hardware_design.json", artifact_names)
        self.assertIn("main.c", artifact_names)
        for artifact in detail.json()["artifacts"]:
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")

        approved = self.client.post(
            f"/api/v1/embedded/tasks/{task_id}/approval",
            json={"decision": "approve", "actor": "reviewer"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "COMPLETED")
        self.assertEqual(approved.json()["validation_state"], "PASSED")
        self.assertIn("PASSED", approved.json()["final_report"])

    def test_rejection_marks_task_failed(self) -> None:
        created = self.client.post(
            "/api/v1/embedded/tasks", json={"goal": "ESP32 节点"}
        ).json()
        rejected = self.client.post(
            f"/api/v1/embedded/tasks/{created['task_id']}/approval",
            json={"decision": "reject", "actor": "reviewer"},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["status"], "FAILED")
        self.assertIn("APPROVAL_REJECTED", rejected.json()["error"])

    def test_task_listing_and_404(self) -> None:
        self.client.post("/api/v1/embedded/tasks", json={"goal": "ESP32 节点"})
        listed = self.client.get("/api/v1/embedded/tasks")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        missing = self.client.get("/api/v1/embedded/tasks/emb-999999")
        self.assertEqual(missing.status_code, 404)
        conflict = self.client.post(
            "/api/v1/embedded/tasks/emb-999999/approval",
            json={"decision": "approve", "actor": "x"},
        )
        self.assertEqual(conflict.status_code, 404)

    def test_approval_conflict_when_not_awaiting(self) -> None:
        created = self.client.post(
            "/api/v1/embedded/tasks", json={"goal": "ESP32 节点"}
        ).json()
        self.client.post(
            f"/api/v1/embedded/tasks/{created['task_id']}/approval",
            json={"decision": "reject", "actor": "reviewer"},
        )
        second = self.client.post(
            f"/api/v1/embedded/tasks/{created['task_id']}/approval",
            json={"decision": "approve", "actor": "reviewer"},
        )
        self.assertEqual(second.status_code, 409)

    def test_invalid_decision_rejected_by_schema(self) -> None:
        created = self.client.post(
            "/api/v1/embedded/tasks", json={"goal": "ESP32 节点"}
        ).json()
        invalid = self.client.post(
            f"/api/v1/embedded/tasks/{created['task_id']}/approval",
            json={"decision": "maybe", "actor": "x"},
        )
        self.assertEqual(invalid.status_code, 422)
        blank = self.client.post(
            "/api/v1/embedded/tasks", json={"goal": " "}
        )
        self.assertEqual(blank.status_code, 422)


class CapabilityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def tearDown(self) -> None:
        self.client._tmp.cleanup()  # type: ignore[attr-defined]

    def test_lists_capabilities_with_filters(self) -> None:
        all_caps = self.client.get("/api/v1/capabilities")
        self.assertEqual(all_caps.status_code, 200)
        names = {(item["name"], item["version"]) for item in all_caps.json()}
        self.assertIn(("esp32_compile", "2.0"), names)

        firmware = self.client.get(
            "/api/v1/capabilities", params={"type": "firmware"}
        )
        self.assertTrue(all(item["type"] == "firmware" for item in firmware.json()))
        self.assertEqual(len(firmware.json()), 2)

        idf = self.client.get(
            "/api/v1/capabilities",
            params={"type": "firmware", "version": "2.0"},
        )
        # version filter is advisory; type+permission are the hard filters
        self.assertEqual(firmware.status_code, 200)

        knowledge = self.client.get(
            "/api/v1/capabilities", params={"permission": "EMBEDDED_READ"}
        )
        self.assertEqual(
            {item["name"] for item in knowledge.json()}, {"mcu_catalog_lookup"}
        )


if __name__ == "__main__":
    unittest.main()
