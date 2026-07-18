"""Pure deterministic policy evaluation."""

from __future__ import annotations

from hashlib import sha256
import json

from network_agent_rag.policy.models import (
    PolicyCondition,
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
)
from network_agent_rag.policy.registry import PolicyRegistry


_PRECEDENCE = {
    PolicyEffect.ALLOW: 1,
    PolicyEffect.REQUIRE_APPROVAL: 2,
    PolicyEffect.DENY: 3,
}


class PolicyEngine:
    def __init__(self, registry: PolicyRegistry) -> None:
        self.registry = registry

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        matched = tuple(
            rule for rule in self.registry.rules if _matches(rule.condition, context)
        )
        decision = (
            max((rule.effect for rule in matched), key=_PRECEDENCE.__getitem__)
            if matched
            else PolicyEffect.DENY
        )
        matched_rules = tuple(rule.rule_id for rule in matched)
        reasons = (
            tuple(f"matched:{rule.rule_id}:{rule.effect.value}" for rule in matched)
            if matched
            else ("no_matching_rule:default_deny",)
        )
        payload = {
            "context": context.model_dump(mode="json"),
            "matched_rules": matched_rules,
            "decision": decision.value,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return PolicyDecision(
            decision_id=sha256(encoded).hexdigest(),
            decision=decision,
            reasons=reasons,
            matched_rules=matched_rules,
            timestamp=context.timestamp,
        )


def _matches(condition: PolicyCondition, context: PolicyContext) -> bool:
    return (
        (condition.operation is None or context.operation in condition.operation)
        and (condition.risk_level is None or context.risk_level in condition.risk_level)
        and (condition.permission is None or context.permission in condition.permission)
        and (
            condition.role is None
            or bool(set(context.roles).intersection(condition.role))
        )
        and (
            condition.min_devices is None
            or len(context.devices) >= condition.min_devices
        )
        and (
            condition.max_devices is None
            or len(context.devices) <= condition.max_devices
        )
    )


__all__ = ["PolicyEngine"]
