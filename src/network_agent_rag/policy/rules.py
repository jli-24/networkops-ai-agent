"""Default fail-closed enterprise policy rules."""

from __future__ import annotations

from network_agent_rag.auth import Permission
from network_agent_rag.policy.models import (
    PolicyCondition,
    PolicyEffect,
    PolicyOperation,
    PolicyRiskLevel,
    PolicyRule,
)
from network_agent_rag.policy.registry import PolicyRegistry


def default_policy_registry() -> PolicyRegistry:
    unprivileged = frozenset(
        permission
        for permission in Permission
        if permission != Permission.EXECUTE_REPAIR
    )
    rules = (
        PolicyRule(
            rule_id="allow-low-risk-device-status",
            name="Allow low-risk device status",
            condition=PolicyCondition(
                operation=frozenset({PolicyOperation.VIEW_DEVICE_STATUS}),
                risk_level=frozenset({PolicyRiskLevel.LOW}),
                permission=frozenset({Permission.EXECUTE_REPAIR}),
            ),
            effect=PolicyEffect.ALLOW,
        ),
        PolicyRule(
            rule_id="deny-unprivileged-batch-change",
            name="Deny unprivileged batch changes",
            condition=PolicyCondition(
                operation=frozenset(
                    {PolicyOperation.BATCH_CONFIGURATION_CHANGE}
                ),
                permission=unprivileged,
            ),
            effect=PolicyEffect.DENY,
        ),
        PolicyRule(
            rule_id="require-approval-high-risk-change",
            name="Require approval for high-risk changes",
            condition=PolicyCondition(
                operation=frozenset(
                    {
                        PolicyOperation.RESTART_DEVICE,
                        PolicyOperation.BATCH_CONFIGURATION_CHANGE,
                    }
                ),
                risk_level=frozenset(
                    {PolicyRiskLevel.HIGH, PolicyRiskLevel.CRITICAL}
                ),
                permission=frozenset({Permission.EXECUTE_REPAIR}),
            ),
            effect=PolicyEffect.REQUIRE_APPROVAL,
        ),
    )
    return PolicyRegistry(
        rules,
        {
            "network.view_device_status": PolicyOperation.VIEW_DEVICE_STATUS,
            "network.restart_device": PolicyOperation.RESTART_DEVICE,
            "network.batch_configuration_change": (
                PolicyOperation.BATCH_CONFIGURATION_CHANGE
            ),
        },
    )


__all__ = ["default_policy_registry"]
