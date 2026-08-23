"""Generate golden v0.15-layout checkpoint fixtures for migration evidence.

MUST be run against the **v0.15 module layout** (tag v0.15.0 / before the
embedded pack migration). The fixtures are the artifacts of record for the
claim "v0.15 checkpoints (incl. interrupted approvals) remain recoverable
after the pack migration" -- because strict msgpack fails loudly on READ,
not on WRITE, an old-layout fixture is the only non-circular proof.

Produces under tests/fixtures/:
- v015_embedded_approval.sqlite3 : workflow paused at the approval
  interrupt (hardest case: state + interrupt payload double decode)
- v015_embedded_midflight.sqlite3 : checkpoint after HardwareAgent only
  (hardware_design present, firmware not yet generated)

A fixed clock is baked into both generation and the recovery test so the
approval TTL check cannot rot as wall-clock time passes.

Usage (from repo root, v0.15 layout):
    .venv/Scripts/python.exe scripts/generate_v015_checkpoint_fixture.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from network_agent_rag.agents.embedded.workflow import create_embedded_workflow
from network_agent_rag.capability import CapabilityRegistry
from network_agent_rag.capability.defaults import register_default_capabilities
from network_agent_rag.infrastructure.simulation import InProcessSimulatorBackend

FIXED_CLOCK = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
GOAL = "设计一个ESP32温湿度采集节点并验证"
TASK_ID = "emb-fix-0001"


def build_graph(checkpointer):
    return create_embedded_workflow(
        backend=InProcessSimulatorBackend(),
        capability_registry=register_default_capabilities(CapabilityRegistry()),
        checkpointer=checkpointer,
        clock=lambda: FIXED_CLOCK,
    )


def generate_approval_fixture(path: Path) -> None:
    path.unlink(missing_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": "golden-approval"}}
        graph.invoke({"task_id": TASK_ID, "goal": GOAL}, config)
        snapshot = graph.get_state(config)
        assert snapshot.next == ("Approval",), snapshot.next
        interrupt_payload = snapshot.tasks[0].interrupts[0].value
        assert "approval_request" in interrupt_payload
    print(f"approval fixture written: {path}")


def generate_midflight_fixture(path: Path) -> None:
    path.unlink(missing_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": "golden-midflight"}}
        for event in graph.stream(
            {"task_id": TASK_ID, "goal": GOAL}, config, stream_mode="updates"
        ):
            # Stop right after the hardware node's checkpoint lands.
            if "HardwareAgent" in event:
                break
        snapshot = graph.get_state(config)
        values = snapshot.values
        assert values.get("hardware_design") is not None
        assert values.get("firmware") is None
    print(f"midflight fixture written: {path}")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    generate_approval_fixture(FIXTURES / "v015_embedded_approval.sqlite3")
    generate_midflight_fixture(FIXTURES / "v015_embedded_midflight.sqlite3")


if __name__ == "__main__":
    main()
