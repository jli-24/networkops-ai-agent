"""Log specialist for the multi-agent workflow."""

from __future__ import annotations

from collections.abc import Callable

from network_agent_rag.agents.diagnosis_workflow import DiagnosisPlan
from network_agent_rag.agents.multi_agent.state import MultiAgentState, completion_update


def run_log_agent(
    state: MultiAgentState,
    retrieve_logs: Callable[[DiagnosisPlan], dict[str, object]] | None,
) -> dict[str, object]:
    if retrieve_logs is None:
        raise ValueError("retrieve_logs is required for LogAgent")
    result = retrieve_logs(state["task_plan"]["analysis"])
    if not isinstance(result, dict):
        raise ValueError("retrieve_logs must return a dict")
    if result.get("ok") is False:
        raise ValueError(
            f"log query failed: {result.get('error_code', 'UNKNOWN')}: "
            f"{result.get('message', '')}"
        )
    records = result.get("records", [])
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError("retrieve_logs records must be a list of dictionaries")
    copied = [dict(record) for record in records]
    return {
        **completion_update(state, "logs"),
        "log_evidence": copied,
    }


__all__ = ["run_log_agent"]
