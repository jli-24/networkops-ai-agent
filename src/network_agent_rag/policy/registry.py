"""Immutable startup registry for rules and explicit tool classifications."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from network_agent_rag.policy.models import PolicyOperation, PolicyRule


@dataclass(frozen=True, slots=True, init=False)
class PolicyRegistry:
    _rules: tuple[PolicyRule, ...]
    _tool_operations: Mapping[str, PolicyOperation]

    def __init__(
        self,
        rules: Iterable[PolicyRule],
        tool_operations: Mapping[str, PolicyOperation],
    ) -> None:
        loaded = tuple(rules)
        identifiers = [rule.rule_id for rule in loaded]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate policy rule_id")
        normalized: dict[str, PolicyOperation] = {}
        for raw_name, operation in tool_operations.items():
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError("tool names must be non-empty strings")
            if not isinstance(operation, PolicyOperation) or operation == PolicyOperation.UNKNOWN:
                raise ValueError("tool operations must be explicit known PolicyOperation values")
            name = raw_name.strip()
            if name in normalized:
                raise ValueError("duplicate tool name")
            normalized[name] = operation
        object.__setattr__(
            self,
            "_rules",
            tuple(sorted(loaded, key=lambda rule: rule.rule_id)),
        )
        object.__setattr__(
            self,
            "_tool_operations",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    @property
    def rules(self) -> tuple[PolicyRule, ...]:
        return self._rules

    @property
    def tool_operations(self) -> Mapping[str, PolicyOperation]:
        return self._tool_operations

    def get_rule(self, rule_id: str) -> PolicyRule | None:
        return next((rule for rule in self._rules if rule.rule_id == rule_id), None)

    def resolve_operation(self, tool_name: str) -> PolicyOperation:
        if not isinstance(tool_name, str):
            return PolicyOperation.UNKNOWN
        return self._tool_operations.get(tool_name.strip(), PolicyOperation.UNKNOWN)


__all__ = ["PolicyRegistry"]
