"""Topology specialist for the multi-agent workflow."""

from __future__ import annotations

from collections.abc import Callable

from network_agent_rag.agents.diagnosis_workflow import DiagnosisPlan
from network_agent_rag.agents.multi_agent.state import MultiAgentState, completion_update


def run_topology_agent(
    state: MultiAgentState,
    retrieve_topology: Callable[[DiagnosisPlan], dict[str, object]] | None,
) -> dict[str, object]:
    if retrieve_topology is None:
        raise ValueError("retrieve_topology is required for TopologyAgent")
    context = retrieve_topology(state["task_plan"]["analysis"])
    if not isinstance(context, dict):
        raise ValueError("retrieve_topology must return a dict")
    return {**completion_update(state, "topology"), "topology_context": context}


__all__ = ["run_topology_agent"]
