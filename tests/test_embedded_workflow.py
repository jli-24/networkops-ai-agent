"""Embedded fixed-graph workflow tests (approval interrupt and retries)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from network_agent_rag.packs.embeddedops.agents.workflow import create_embedded_workflow
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.capability import CapabilityRegistry
from network_agent_rag.packs import PackRegistry
from network_agent_rag.packs.embeddedops.capabilities import bind_embeddedops_handlers
from network_agent_rag.packs.embeddedops.manifest import EMBEDDEDOPS_PACK
from network_agent_rag.packs.embeddedops.domain import (
    DebugReport,
    FirmwareArtifact,
    Framework,
)
from network_agent_rag.packs.embeddedops.simulation import InProcessSimulatorBackend
from network_agent_rag.packs.embeddedops.domain.models import ValidationState


GOOD_SOURCE = "/* fw */\nvoid app_main(void) { sample_and_report(); }\n"


def _build_graph(
    *,
    checkpointer=None,
    generate_firmware=None,
    diagnose=None,
    audit_log=None,
):
    registry = CapabilityRegistry()
    PackRegistry(capability_registry=registry).register(EMBEDDEDOPS_PACK)
    bind_embeddedops_handlers(registry)
    return create_embedded_workflow(
        backend=InProcessSimulatorBackend(),
        capability_registry=registry,
        generate_firmware=generate_firmware,
        diagnose=diagnose,
        audit_log=audit_log,
        checkpointer=checkpointer,
    )


def _run_with_approval(graph, config, decision: str) -> dict:
    first = graph.invoke({"task_id": "emb-000001", "goal": "ESP32 温湿度节点"}, config)
    __import__("langgraph").errors  # ensure errors module loaded
    resumed = graph.invoke(
        Command(resume={"decision": decision, "actor": "reviewer"}), config
    )
    return resumed if decision == "approve" else resumed


class EmbeddedWorkflowTests(unittest.TestCase):
    def _config(self, thread: str) -> dict:
        return {"configurable": {"thread_id": thread}}

    def test_full_pass_with_approval(self) -> None:
        checkpointer = InMemorySaver()
        graph = _build_graph(checkpointer=checkpointer)
        config = self._config("thread-1")
        graph.invoke({"task_id": "emb-000001", "goal": "ESP32 温湿度节点"}, config)
        final = graph.invoke(
            Command(resume={"decision": "approve", "actor": "reviewer"}), config
        )
        self.assertEqual(final["validation_state"], ValidationState.PASSED.value)
        self.assertIn("PASSED", final["final_report"])
        self.assertEqual(final["approval_result"]["decision"], "approve")
        approval_request = final["approval_request"]
        self.assertEqual(approval_request["action"], "simulation_run")
        self.assertGreaterEqual(len(approval_request["execution_plan"]), 3)

    def test_rejection_terminates_without_simulation(self) -> None:
        checkpointer = InMemorySaver()
        graph = _build_graph(checkpointer=checkpointer)
        config = self._config("thread-2")
        graph.invoke({"task_id": "emb-000002", "goal": "ESP32 节点"}, config)
        final = graph.invoke(
            Command(resume={"decision": "reject", "actor": "reviewer"}), config
        )
        self.assertEqual(final["validation_state"], ValidationState.FAILED.value)
        self.assertIn("APPROVAL_REJECTED", final["error"])
        self.assertIsNone(final["simulation"])

    def test_compile_failure_debug_retry_reaches_pass(self) -> None:
        def generate_firmware(design):
            return FirmwareArtifact(
                artifact_id="fw-bad",
                design_id=design.design_id,
                framework=Framework.ESP_IDF,
                source="int broken;",  # no entry point -> compile error
            )

        checkpointer = InMemorySaver()
        graph = _build_graph(
            checkpointer=checkpointer, generate_firmware=generate_firmware
        )
        config = self._config("thread-3")
        graph.invoke({"task_id": "emb-000003", "goal": "ESP32 节点"}, config)
        final = graph.invoke(
            Command(resume={"decision": "approve", "actor": "reviewer"}), config
        )
        self.assertEqual(final["validation_state"], ValidationState.PASSED.value)
        self.assertEqual(final["iteration"], 1)
        self.assertIn("int main", final["firmware"]["source"])

    def test_infrastructure_error_finishes_without_debug(self) -> None:
        def generate_firmware(design):
            return FirmwareArtifact(
                artifact_id="fw-infra",
                design_id=design.design_id,
                framework=Framework.ESP_IDF,
                source=GOOD_SOURCE + " // SIM_INFRA_TIMEOUT",
            )

        def diagnose(context):  # pragma: no cover - must not be called
            raise AssertionError("debug agent must not run for infra errors")

        checkpointer = InMemorySaver()
        graph = _build_graph(
            checkpointer=checkpointer,
            generate_firmware=generate_firmware,
            diagnose=diagnose,
        )
        config = self._config("thread-4")
        graph.invoke({"task_id": "emb-000004", "goal": "ESP32 节点"}, config)
        final = graph.invoke(
            Command(resume={"decision": "approve", "actor": "reviewer"}), config
        )
        self.assertEqual(final["validation_state"], ValidationState.FAILED.value)
        self.assertIn("INFRASTRUCTURE_ERROR", final["error"])
        self.assertEqual(final["iteration"], 0)
        self.assertIsNone(final["debug_report"])

    def test_invalid_approval_response_rejected(self) -> None:
        checkpointer = InMemorySaver()
        graph = _build_graph(checkpointer=checkpointer)
        config = self._config("thread-5")
        graph.invoke({"task_id": "emb-000005", "goal": "ESP32 节点"}, config)
        with self.assertRaises(ValueError):
            graph.invoke(Command(resume={"decision": "maybe"}), config)

    def test_blank_goal_rejected_at_entry(self) -> None:
        graph = _build_graph()
        with self.assertRaises(ValueError):
            graph.invoke({"goal": "   "}, self._config("thread-6"))

    def test_audit_records_approval_and_agents(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            audit = SQLiteAuditLog(Path(tmp) / "audit.sqlite3")
            checkpointer = InMemorySaver()
            graph = _build_graph(checkpointer=checkpointer, audit_log=audit)
            config = self._config("thread-7")
            graph.invoke({"task_id": "emb-000007", "goal": "ESP32 节点"}, config)
            graph.invoke(
                Command(resume={"decision": "approve", "actor": "reviewer"}), config
            )
            events = audit.list_events("embedded")
            actions = {event.action for event in events}
            self.assertIn("hardware_design", actions)
            self.assertIn("approval_request", actions)
            self.assertIn("approval_decide", actions)
            self.assertIn("verification_pass", actions)


if __name__ == "__main__":
    unittest.main()
