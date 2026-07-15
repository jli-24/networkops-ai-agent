"""Supervisor planning and dependency-aware routing."""

from __future__ import annotations

from network_agent_rag.agents.diagnosis_workflow import validate_diagnosis_plan
from network_agent_rag.agents.multi_agent.state import AgentName, SupervisorPlan


_AGENTS: tuple[AgentName, ...] = (
    "topology",
    "logs",
    "diagnosis",
    "repair",
    "report",
)


def validate_supervisor_plan(plan: object) -> None:
    if not isinstance(plan, dict):
        raise ValueError("Supervisor plan must be a dictionary")
    if "analysis" not in plan:
        raise ValueError("Supervisor plan analysis is required")
    validate_diagnosis_plan(plan["analysis"])
    agents = plan.get("required_agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Supervisor plan required_agents must be a non-empty list")
    if any(agent not in _AGENTS for agent in agents):
        raise ValueError(f"required_agents must contain only {list(_AGENTS)}")
    if len(agents) != len(set(agents)):
        raise ValueError("required_agents must not contain duplicates")
    if "diagnosis" not in agents:
        raise ValueError("diagnosis agent is required")
    if agents[-1] != "report":
        raise ValueError("report must be last")
    requires_repair = plan.get("requires_repair")
    if not isinstance(requires_repair, bool):
        raise ValueError("requires_repair must be a bool")
    if requires_repair != ("repair" in agents):
        raise ValueError("repair agent must match requires_repair")
    positions = {agent: index for index, agent in enumerate(agents)}
    expected = [agent for agent in _AGENTS if agent in positions]
    if agents != expected:
        raise ValueError("required_agents must follow dependency order")
    sources = set(plan["analysis"]["required_sources"])
    for source, agent in (("topology", "topology"), ("logs", "logs")):
        if source in sources and agent not in agents:
            raise ValueError(f"{source} evidence requires {agent} agent")
        if agent in agents and source not in sources:
            raise ValueError(f"{agent} agent requires {source} evidence")


def next_agent(plan: SupervisorPlan, pending: list[AgentName]) -> AgentName | None:
    """Choose the first pending agent in the validated dependency order."""

    return next((agent for agent in plan["required_agents"] if agent in pending), None)


__all__ = ["next_agent", "validate_supervisor_plan"]
