"""Development-only Enterprise Workflow factory for the local showcase."""

from __future__ import annotations

from typing import Any

from network_agent_rag.agents.enterprise import create_enterprise_workflow
from network_agent_rag.agents.multi_agent.state import SupervisorPlan


def create_workflow(
    checkpointer: Any,
    *,
    policy_engine: Any,
    audit_log: Any = None,
    trace_store: Any = None,
    metrics_store: Any = None,
    trace_collector: Any = None,
):
    """Compile the existing workflow with deterministic local-only planning."""
    del metrics_store
    return create_enterprise_workflow(
        checkpointer=checkpointer,
        plan_incident=_plan_incident,
        policy_engine=policy_engine,
        audit_log=audit_log,
        trace_store=trace_store,
        trace_collector=trace_collector,
    )


def _plan_incident(query: str, incident_id: str) -> SupervisorPlan:
    del incident_id
    return {
        "analysis": {
            "devices": ["SW-01", "SERVER-01"],
            "interfaces": ["SW-01:uplink", "SERVER-01:eth0"],
            "symptom": query,
            "start_time": "1970-01-01T00:00:00+00:00",
            "end_time": "1970-01-01T00:00:00+00:00",
            "required_sources": [],
        },
        "required_agents": ["diagnosis", "report"],
        "requires_repair": False,
    }


__all__ = ["create_workflow"]
