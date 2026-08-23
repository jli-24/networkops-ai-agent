"""Golden pre-migration network checkpoints recovered under the pack layout.

Evidence for RFC sec 11 (P0-2): fixtures were serialized under the old
module layout; strict msgpack fails on READ not WRITE, so loading them
here is the non-circular proof that pre-migration network checkpoints
(incl. approval interrupts and datetime risk decisions) survive.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.packs.networkops.agents.enterprise import (
    AllowlistedExecutor,
    create_enterprise_workflow,
)
from tests.test_multi_agent_workflow import (
    documents,
    grade,
    incident_plan,
    logs,
    metrics,
    topology,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_CLOCK = datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc)


def _arguments(checkpointer, audit: SQLiteAuditLog, calls: list[str]):
    return {
        "checkpointer": checkpointer,
        "audit_log": audit,
        "plan_incident": lambda query, incident_id: incident_plan(),
        "retrieve_topology": topology,
        "retrieve_logs": logs,
        "retrieve_metrics": metrics,
        "retrieve_documents": documents,
        "grade_documents": grade,
        "rewrite_query": lambda state: f"{state['rewritten_query']} CRC",
        "plan_actions": lambda state: [
            {
                "action_id": "ACTION-1",
                "tool_name": "digital_twin.update_link",
                "target": "SW1--SW2",
                "arguments": {"status": "up"},
                "verification_steps": ["verify packet loss"],
                "rollback_instructions": "restore previous status",
            }
        ],
        "action_executor": AllowlistedExecutor(
            {
                "digital_twin.update_link": lambda item: calls.append(
                    item["action_id"]
                )
                or {"status": "succeeded", "message": "link restored"}
            }
        ),
        "clock": lambda: FIXED_CLOCK,
    }


class GoldenNetworkCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _copy(self, name: str) -> Path:
        target = self._root / name
        shutil.copy(FIXTURES / name, target)
        audit_name = name.replace(".sqlite3", ".audit.sqlite3")
        shutil.copy(FIXTURES / audit_name, self._root / audit_name)
        return target

    def test_enterprise_approval_interrupt_recovers_and_executes(self) -> None:
        checkpoint = self._copy("v016_enterprise_approval.sqlite3")

        async def run() -> tuple[dict, list[str]]:
            audit = SQLiteAuditLog(
                self._root / "v016_enterprise_approval.audit.sqlite3"
            )
            calls: list[str] = []
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = create_enterprise_workflow(
                    **_arguments(saver, audit, calls)
                )
                config = {"configurable": {"thread_id": "golden-enterprise"}}
                snapshot = await graph.aget_state(config)
                values = snapshot.values
                self.assertEqual(values["incident_id"], "GOLDEN-NET-001")
                self.assertEqual(values["enterprise_status"], "pending_approval")
                risk = values["risk_decision"]
                self.assertIsInstance(risk["expires_at"], datetime)
                self.assertTrue(risk["approval_required"])
                self.assertGreaterEqual(len(values["proposed_actions"]), 1)

                final = await graph.ainvoke(
                    Command(
                        resume={
                            "decision": "approve",
                            "actor": "reviewer",
                            "plan_digest": risk["plan_digest"],
                        }
                    ),
                    config,
                )
                return final, calls

        final, calls = asyncio.run(run())
        self.assertEqual(final["enterprise_status"], "executed")
        self.assertEqual(final["approval_result"]["decision"], "approve")
        self.assertEqual(calls, ["ACTION-1"])

    def test_enterprise_midflight_recovers_and_runs_to_report(self) -> None:
        checkpoint = self._copy("v016_enterprise_midflight.sqlite3")

        async def run() -> dict:
            audit = SQLiteAuditLog(
                self._root / "v016_enterprise_midflight.audit.sqlite3"
            )
            calls: list[str] = []
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = create_enterprise_workflow(
                    **_arguments(saver, audit, calls)
                )
                config = {"configurable": {"thread_id": "golden-enterprise-mid"}}
                snapshot = await graph.aget_state(config)
                self.assertTrue(snapshot.next)
                self.assertIsNotNone(snapshot.values["task_plan"])
                self.assertIsNone(snapshot.values.get("approval_result"))

                # Continue to the approval interrupt, then approve.
                await graph.ainvoke(None, config)
                snapshot = await graph.aget_state(config)
                risk = snapshot.values["risk_decision"]
                final = await graph.ainvoke(
                    Command(
                        resume={
                            "decision": "approve",
                            "actor": "reviewer",
                            "plan_digest": risk["plan_digest"],
                        }
                    ),
                    config,
                )
                return final

        final = asyncio.run(run())
        self.assertEqual(final["enterprise_status"], "executed")


if __name__ == "__main__":
    unittest.main()
