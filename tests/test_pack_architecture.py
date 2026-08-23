"""Architecture guard: core must not import domain packs (acceptance #3).

Also maintains the burn-down inventory of network-domain modules still
living in core. After the embedded migration (v0.16 step 2) the embedded
entries are gone and every network module is listed; step 3 empties this
list as its completion condition.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "network_agent_rag"

ASSEMBLY_MODULES = frozenset({"network_agent_rag.main"})

# Burn-down COMPLETE (v0.16 step 3): every network domain module now lives
# in packs/networkops. Kept as an explicit (empty) registry so future
# domain leakage into core has an obvious place to be caught.
NETWORK_STILL_IN_CORE: tuple[str, ...] = ()

# Platform facilities that deliberately remain in core (dual-list
# mechanism, see docs/AGENTOS_CONTEXT.md); adjudication windows tracked there.
PLATFORM_FACILITIES_REMAINING = (
    "network_agent_rag.api.benchmarks",
    "network_agent_rag.api.observability",
)

# Domain assets that must live ONLY in their packs (steps 2+3).
EMBEDDED_PATHS_REMOVED = (
    "network_agent_rag.agents.embedded",
    "network_agent_rag.domain.embedded",
    "network_agent_rag.infrastructure",
    "network_agent_rag.infrastructure.simulation",
    "network_agent_rag.capability.defaults",
    "network_agent_rag.rag.embedded_corpus",
    "network_agent_rag.api.embedded",
    "network_agent_rag.evaluation.embedded_cases",
)
NETWORK_PATHS_REMOVED = (
    "network_agent_rag.agents",
    "network_agent_rag.agents.diagnosis_workflow",
    "network_agent_rag.agents.enterprise",
    "network_agent_rag.agents.log_tools",
    "network_agent_rag.agents.monitoring_tools",
    "network_agent_rag.agents.multi_agent",
    "network_agent_rag.agents.workflow",
    "network_agent_rag.api.router",
    "network_agent_rag.api.schemas",
    "network_agent_rag.api.history",
    "network_agent_rag.api.console",
    "network_agent_rag.api.enterprise",
    "network_agent_rag.api.enterprise_schemas",
    "network_agent_rag.domain.topology",
    "network_agent_rag.digital_twin",
    "network_agent_rag.frontend",
    "network_agent_rag.demo",
    "network_agent_rag.demo_main",
    "network_agent_rag.multi_agent_demo",
    "network_agent_rag.multi_agent_main",
)



def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC.parent)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _iter_core_modules():
    for path in SRC.rglob("*.py"):
        if "packs" in path.relative_to(SRC).parts:
            continue  # pack subtree + pack runtime
        name = _module_name(path)
        if name in ASSEMBLY_MODULES:
            continue
        yield path, name


def _imported_modules(tree: ast.Module) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets


class CoreIsolationTests(unittest.TestCase):
    def test_core_modules_do_not_import_packs(self) -> None:
        violations: list[str] = []
        for path, name in _iter_core_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for target in _imported_modules(tree):
                if target.startswith("network_agent_rag.packs"):
                    violations.append(f"{name} -> {target}")
        self.assertEqual(
            violations,
            [],
            "core modules must not import domain packs "
            "(only network_agent_rag.main may, as the assembly layer)",
        )

    @staticmethod
    def _spec(module: str):
        # find_spec raises (not returns None) when a parent package is gone.
        try:
            return importlib.util.find_spec(module)
        except ModuleNotFoundError:
            return None

    def test_old_embedded_module_paths_are_gone(self) -> None:
        present = [
            module
            for module in EMBEDDED_PATHS_REMOVED
            if self._spec(module) is not None
        ]
        self.assertEqual(present, [], "embedded assets must live in the pack only")

    def test_old_network_module_paths_are_gone(self) -> None:
        present = [
            module
            for module in NETWORK_PATHS_REMOVED
            if self._spec(module) is not None
        ]
        self.assertEqual(present, [], "network assets must live in the pack only")

    def test_network_burn_down_is_complete(self) -> None:
        self.assertEqual(NETWORK_STILL_IN_CORE, ())

    def test_platform_facilities_stay_in_core(self) -> None:
        missing = [
            module
            for module in PLATFORM_FACILITIES_REMAINING
            if self._spec(module) is None
        ]
        self.assertEqual(missing, [])

    def test_both_packs_registered_in_default_app(self) -> None:
        from fastapi.testclient import TestClient

        from network_agent_rag.main import create_app

        client = TestClient(create_app())
        self.assertEqual(client.get("/api/v1/health").status_code, 200)

    def test_embedded_pack_exists(self) -> None:
        from network_agent_rag.packs.embeddedops import EMBEDDEDOPS_PACK

        self.assertEqual(EMBEDDEDOPS_PACK.name, "embeddedops")
        self.assertTrue(EMBEDDEDOPS_PACK.permissions)
        self.assertTrue(EMBEDDEDOPS_PACK.roles)


if __name__ == "__main__":
    unittest.main()
