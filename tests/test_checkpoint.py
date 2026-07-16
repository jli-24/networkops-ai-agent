"""Persistence and interruption tests for the enterprise workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from network_agent_rag.agents.enterprise import (
    AllowlistedExecutor,
    create_enterprise_workflow,
)
from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.api.enterprise import create_sqlite_enterprise_app
from tests.test_multi_agent_workflow import (
    documents,
    grade,
    incident_plan,
    logs,
    metrics,
    topology,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def build_graph(
    checkpointer,
    audit: SQLiteAuditLog,
    calls: list[str],
    trace_store=None,
    trace_collector=None,
    **overrides,
):
    arguments = {
        "checkpointer": checkpointer,
        "audit_log": audit,
        "trace_store": trace_store,
        "trace_collector": trace_collector,
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
        "clock": lambda: NOW,
    }
    arguments.update(overrides)
    return create_enterprise_workflow(**arguments)


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_checkpoint_resumes_after_reopening_database(self) -> None:
        with TemporaryDirectory() as directory:
            checkpoint_path = str(Path(directory) / "checkpoints.sqlite3")
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            calls: list[str] = []
            config = {"configurable": {"thread_id": "INC-1001"}}

            async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
                graph = build_graph(saver, audit, calls)
                first = await graph.ainvoke(
                    {"user_query": "分析 SW1 到 SW2 丢包", "incident_id": "INC-1001"},
                    config,
                )
                snapshot = await graph.aget_state(config)
                digest = snapshot.values["risk_decision"]["plan_digest"]

                self.assertIn("__interrupt__", first)
                self.assertEqual(snapshot.values["enterprise_status"], "pending_approval")
                self.assertEqual(calls, [])

            async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
                graph = build_graph(saver, audit, calls)
                final = await graph.ainvoke(
                    Command(
                        resume={
                            "decision": "approve",
                            "actor": "noc-operator",
                            "plan_digest": digest,
                        }
                    ),
                    config,
                )

                self.assertEqual(final["enterprise_status"], "executed")
                self.assertEqual(final["execution_result"]["status"], "succeeded")
                self.assertEqual(calls, ["ACTION-1"])
                self.assertIn("link restored", final["answer"])

    async def test_incident_threads_are_isolated(self) -> None:
        with TemporaryDirectory() as directory:
            calls: list[str] = []
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            async with AsyncSqliteSaver.from_conn_string(
                str(Path(directory) / "checkpoints.sqlite3")
            ) as saver:
                graph = build_graph(saver, audit, calls)
                for incident_id in ("INC-1001", "INC-2002"):
                    await graph.ainvoke(
                        {"user_query": incident_id, "incident_id": incident_id},
                        {"configurable": {"thread_id": incident_id}},
                    )

                first = await graph.aget_state(
                    {"configurable": {"thread_id": "INC-1001"}}
                )
                second = await graph.aget_state(
                    {"configurable": {"thread_id": "INC-2002"}}
                )

                self.assertEqual(first.values["incident_id"], "INC-1001")
                self.assertEqual(second.values["incident_id"], "INC-2002")
                self.assertNotEqual(first.values["user_query"], second.values["user_query"])

    def test_app_lifespan_supports_legacy_and_observed_factories(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            create_sqlite_enterprise_app()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            create_sqlite_enterprise_app(
                workflow_factory=lambda saver, audit: object(),
                observed_workflow_factory=lambda saver, **dependencies: object(),
            )

        with TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoints.sqlite3"
            audit_path = Path(directory) / "audit.sqlite3"
            observability_path = Path(directory) / "observability.sqlite3"
            benchmark_results_path = Path(directory) / "benchmark-results"
            calls: list[str] = []
            received: dict[str, object] = {}

            def observed_factory(
                saver,
                *,
                audit_log=None,
                trace_store=None,
                metrics_store=None,
                trace_collector=None,
            ):
                received.update(
                    {
                        "audit_log": audit_log,
                        "trace_store": trace_store,
                        "metrics_store": metrics_store,
                        "trace_collector": trace_collector,
                    }
                )
                return build_graph(
                    saver,
                    audit_log,
                    calls,
                    trace_store,
                    trace_collector,
                )

            app = create_sqlite_enterprise_app(
                observed_workflow_factory=observed_factory,
                checkpoint_path=checkpoint_path,
                audit_path=audit_path,
                observability_path=observability_path,
                benchmark_results_path=benchmark_results_path,
                clock=lambda: NOW,
            )

            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/incidents",
                    json={
                        "session_id": "session-1",
                        "incident_id": "INC-APP",
                        "query": "分析丢包",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(checkpoint_path.exists())
                self.assertTrue(audit_path.exists())
                self.assertTrue(observability_path.exists())
                self.assertTrue(benchmark_results_path.is_dir())
                self.assertIsNotNone(received["audit_log"])
                self.assertIsNotNone(received["trace_store"])
                self.assertIsNotNone(received["metrics_store"])
                self.assertIsNotNone(received["trace_collector"])

            legacy_default: list[object] = []

            def legacy_factory(saver, audit, marker="legacy-default"):
                legacy_default.append(marker)
                return build_graph(saver, audit, [])

            legacy_app = create_sqlite_enterprise_app(
                workflow_factory=legacy_factory,
                checkpoint_path=Path(directory) / "legacy-checkpoints.sqlite3",
                audit_path=Path(directory) / "legacy-audit.sqlite3",
                observability_path=Path(directory) / "legacy-observability.sqlite3",
                benchmark_results_path=Path(directory) / "legacy-results",
                clock=lambda: NOW,
            )
            with TestClient(legacy_app):
                pass

            self.assertEqual(legacy_default, ["legacy-default"])


if __name__ == "__main__":
    unittest.main()
