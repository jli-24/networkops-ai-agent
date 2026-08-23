"""Generate golden v0.16(pre-network-move)-layout checkpoint fixtures.

MUST run against the layout BEFORE the network pack migration (i.e. the
commit that adds this script). Same evidence pattern as the v0.15 golden
fixtures (RFC sec 10): strict msgpack fails on READ not WRITE, so an
old-layout fixture is the only non-circular proof that pre-migration
network checkpoints survive the move.

Fixtures under tests/fixtures/:
- v016_enterprise_approval.sqlite3 : enterprise workflow paused at the
  approval interrupt (state + risk_decision with datetime + interrupt
  payload), fixed clock 2026-08-24T08:00Z
- v016_multiagent_midflight.sqlite3 : multi-agent graph paused after the
  first specialist agent checkpoint

Usage (repo root, pre-migration layout):
    .venv/Scripts/python.exe scripts/generate_v016_network_checkpoint_fixture.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from network_agent_rag.agents.enterprise import (
    AllowlistedExecutor,
    create_enterprise_workflow,
)
from network_agent_rag.audit import SQLiteAuditLog
from tests.test_multi_agent_workflow import (
    documents,
    grade,
    incident_plan,
    logs,
    metrics,
    topology,
)

FIXED_CLOCK = datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
QUERY = "分析 SW1 到 SW2 丢包"


def enterprise_arguments(checkpointer, audit: SQLiteAuditLog, calls: list[str]):
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


def generate_enterprise_approval(path: Path) -> None:
    path.unlink(missing_ok=True)
    audit_path = path.with_suffix(".audit.sqlite3")
    audit_path.unlink(missing_ok=True)
    audit = SQLiteAuditLog(audit_path)
    calls: list[str] = []

    async def run() -> None:
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            graph = create_enterprise_workflow(
                **enterprise_arguments(saver, audit, calls)
            )
            config = {"configurable": {"thread_id": "golden-enterprise"}}
            result = await graph.ainvoke(
                {"user_query": QUERY, "incident_id": "GOLDEN-NET-001"}, config
            )
            assert "__interrupt__" in result, "expected approval interrupt"
            snapshot = await graph.aget_state(config)
            assert snapshot.values["enterprise_status"] == "pending_approval"

    asyncio.run(run())
    print(f"enterprise approval fixture written: {path}")


def generate_enterprise_midflight(path: Path) -> None:
    """Mid-flight checkpoint: stop right after the first specialist node.

    The sync multi-agent factory accepts no checkpointer (and must not be
    changed for fixtures' sake), so the mid-flight sample comes from the
    enterprise graph's DiagnosisAgent checkpoint instead.
    """

    path.unlink(missing_ok=True)
    audit = SQLiteAuditLog(path.with_suffix(".audit.sqlite3"))
    calls: list[str] = []

    async def run() -> None:
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            graph = create_enterprise_workflow(
                **enterprise_arguments(saver, audit, calls)
            )
            config = {"configurable": {"thread_id": "golden-enterprise-mid"}}
            async for event in graph.astream(
                {"user_query": QUERY, "incident_id": "GOLDEN-NET-002"},
                config,
                stream_mode="updates",
            ):
                if "DiagnosisAgent" in event:
                    break
            snapshot = await graph.aget_state(config)
            values = dict(snapshot.values)
            assert snapshot.next, "expected a pending (non-terminal) state"
            assert values.get("task_plan") is not None
            assert values.get("approval_result") is None

    asyncio.run(run())
    print(f"enterprise midflight fixture written: {path}")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    generate_enterprise_approval(FIXTURES / "v016_enterprise_approval.sqlite3")
    generate_enterprise_midflight(FIXTURES / "v016_enterprise_midflight.sqlite3")


if __name__ == "__main__":
    main()
