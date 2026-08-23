"""Repeatable, local-only NetworkOps demonstration scenario."""

from demo.fault_scenarios import (
    INCIDENT_ID,
    LinkFailureScenario,
    build_link_failure_scenario,
)

__all__ = ["INCIDENT_ID", "LinkFailureScenario", "build_link_failure_scenario"]
