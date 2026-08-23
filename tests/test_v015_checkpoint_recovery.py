"""Golden v0.15 checkpoint recovery under the v0.16 pack layout.

Evidence, not self-testimony: the fixtures were serialized under the old
module layout (see scripts/generate_v015_checkpoint_fixture.py). Strict
msgpack fails loudly on READ, not on WRITE, so loading these bytes here
is the only non-circular proof that pre-migration checkpoints -- including
interrupted approvals -- survive the pack migration.

Fixed clock matches fixture generation so the approval TTL cannot rot.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from network_agent_rag.capability import CapabilityRegistry
from network_agent_rag.packs import PackRegistry
from network_agent_rag.packs.embeddedops.agents.workflow import (
    create_embedded_workflow,
)
from network_agent_rag.packs.embeddedops.capabilities import (
    bind_embeddedops_handlers,
)
from network_agent_rag.packs.embeddedops.manifest import EMBEDDEDOPS_PACK
from network_agent_rag.packs.embeddedops.simulation import InProcessSimulatorBackend

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_CLOCK = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _build_graph(checkpointer):
    capability_registry = CapabilityRegistry()
    pack_registry = PackRegistry(capability_registry=capability_registry)
    pack_registry.register(EMBEDDEDOPS_PACK)
    bind_embeddedops_handlers(capability_registry)
    return create_embedded_workflow(
        backend=InProcessSimulatorBackend(),
        capability_registry=capability_registry,
        checkpointer=checkpointer,
        clock=lambda: FIXED_CLOCK,
    )


class GoldenCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _copy_fixture(self, name: str) -> Path:
        target = Path(self._tmp.name) / name
        shutil.copy(FIXTURES / name, target)
        return target

    def test_v015_approval_interrupt_recovers_and_resumes(self) -> None:
        fixture = self._copy_fixture("v015_embedded_approval.sqlite3")
        with SqliteSaver.from_conn_string(str(fixture)) as saver:
            graph = _build_graph(saver)
            config = {"configurable": {"thread_id": "golden-approval"}}

            snapshot = graph.get_state(config)
            values = snapshot.values
            self.assertEqual(values["task_id"], "emb-fix-0001")
            self.assertIn("温湿度", values["goal"])
            # v0.15 fallback quirk preserved by the fixture: the goal text
            # lacks a wifi/云/mqtt keyword, so selection fell through to the
            # lowest-RAM catalog entry. Assert the fact the fixture holds.
            self.assertEqual(
                values["hardware_design"]["mcu"]["model"], "STM32F103C8T6"
            )
            self.assertEqual(values["firmware"]["filename"], "main.c")

            self.assertEqual(snapshot.next, ("Approval",))
            payload = snapshot.tasks[0].interrupts[0].value["approval_request"]
            self.assertEqual(payload["action"], "simulation_run")
            self.assertEqual(payload["artifact_ref"], "main.c")
            self.assertGreaterEqual(len(payload["execution_plan"]), 3)

            final = graph.invoke(
                Command(resume={"decision": "approve", "actor": "reviewer"}), config
            )
            self.assertEqual(final["validation_state"], "PASSED")
            self.assertEqual(final["approval_result"]["decision"], "approve")

    def test_v015_midflight_checkpoint_recovers_and_completes(self) -> None:
        fixture = self._copy_fixture("v015_embedded_midflight.sqlite3")
        with SqliteSaver.from_conn_string(str(fixture)) as saver:
            graph = _build_graph(saver)
            config = {"configurable": {"thread_id": "golden-midflight"}}

            snapshot = graph.get_state(config)
            self.assertIsNotNone(snapshot.values["hardware_design"])
            self.assertIsNone(snapshot.values.get("firmware"))

            # Continue the interrupted run: firmware -> approval interrupt.
            graph.invoke(None, config)
            snapshot = graph.get_state(config)
            self.assertEqual(snapshot.next, ("Approval",))

            final = graph.invoke(
                Command(resume={"decision": "approve", "actor": "reviewer"}), config
            )
            self.assertEqual(final["validation_state"], "PASSED")
            self.assertEqual(final["approval_result"]["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
