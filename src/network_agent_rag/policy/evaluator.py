"""Runtime adapter from repair actions to immutable PolicyContext objects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from network_agent_rag.auth import Permission, UserContext
from network_agent_rag.policy.engine import PolicyEngine
from network_agent_rag.policy.models import (
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
    PolicyRiskLevel,
)


_PRECEDENCE = {
    PolicyEffect.ALLOW: 1,
    PolicyEffect.REQUIRE_APPROVAL: 2,
    PolicyEffect.DENY: 3,
}


@dataclass(frozen=True)
class PolicyEvaluation:
    action_id: str
    context: PolicyContext
    decision: PolicyDecision


def evaluate_actions(
    engine: PolicyEngine,
    user_context: UserContext,
    actions: Sequence[Mapping[str, object]],
    *,
    risk_level: PolicyRiskLevel | str,
    timestamp: datetime,
) -> tuple[PolicyEvaluation, ...]:
    targets = tuple(
        dict.fromkeys(
            str(action.get("target", "")).strip()
            for action in actions
            if str(action.get("target", "")).strip()
        )
    )
    if not targets:
        raise ValueError("policy evaluation requires action targets")
    level = PolicyRiskLevel(risk_level)
    evaluations = []
    for action in actions:
        action_id = action.get("action_id")
        tool_name = action.get("tool_name")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("policy evaluation requires action_id")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("policy evaluation requires tool_name")
        context = PolicyContext(
            actor_id=user_context.user.user_id,
            roles=user_context.user.roles,
            permission=Permission.EXECUTE_REPAIR,
            operation=engine.registry.resolve_operation(tool_name),
            tool_name=tool_name,
            devices=targets,
            risk_level=level,
            timestamp=timestamp,
        )
        evaluations.append(
            PolicyEvaluation(
                action_id=action_id.strip(),
                context=context,
                decision=engine.evaluate(context),
            )
        )
    return tuple(evaluations)


def aggregate_effect(evaluations: Sequence[PolicyEvaluation]) -> PolicyEffect:
    if not evaluations:
        return PolicyEffect.DENY
    return max(
        (evaluation.decision.decision for evaluation in evaluations),
        key=_PRECEDENCE.__getitem__,
    )


__all__ = ["PolicyEvaluation", "aggregate_effect", "evaluate_actions"]
