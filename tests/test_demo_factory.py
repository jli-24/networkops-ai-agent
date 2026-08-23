"""Contracts for the importable local showcase workflow factory."""

from __future__ import annotations

import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class DemoWorkflowFactoryTests(unittest.TestCase):
    def test_factory_is_importable_by_deployment_path(self) -> None:
        module = importlib.import_module("demo.networkops_local_factory")

        from deployment.app import load_workflow_factory

        factory = load_workflow_factory(
            "demo.networkops_local_factory:create_workflow"
        )

        self.assertIs(factory, module.create_workflow)
        self.assertTrue(callable(factory))

    def test_factory_compiles_the_existing_enterprise_workflow(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        from demo.networkops_local_factory import create_workflow
        from network_agent_rag.audit import SQLiteAuditLog
        from network_agent_rag.policy import PolicyEngine, default_policy_registry

        with TemporaryDirectory() as directory:
            graph = create_workflow(
                InMemorySaver(),
                policy_engine=PolicyEngine(default_policy_registry()),
                audit_log=SQLiteAuditLog(Path(directory) / "audit.sqlite3"),
            )

            result = graph.invoke(
                {
                    "user_query": "show local network status",
                    "incident_id": "INC-LOCAL-FACTORY",
                },
                {"configurable": {"thread_id": "INC-LOCAL-FACTORY"}},
            )

        self.assertEqual(result["incident_id"], "INC-LOCAL-FACTORY")
        self.assertEqual(result["enterprise_status"], "running")
        self.assertIn("diagnosis", result["completed_agents"])
        self.assertIn("report", result["completed_agents"])


if __name__ == "__main__":
    unittest.main()
