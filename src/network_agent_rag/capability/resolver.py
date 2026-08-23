"""Goal-to-capability resolution with metadata-aware filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass

from network_agent_rag.auth import Permission
from network_agent_rag.capability.models import Capability, CapabilityType
from network_agent_rag.capability.registry import CapabilityRegistry


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

_STOP_TOKENS = frozenset(
    {
        "a", "an", "and", "build", "compile", "the", "for", "in", "need",
        "needs", "of", "on", "project", "run", "to", "use", "using", "with",
        "i", "my", "me", "please", "want",
    }
)


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN_PATTERN.findall(text.lower())
        if token not in _STOP_TOKENS
    )


@dataclass(frozen=True)
class CapabilityQuery:
    """Filters applied on top of goal-token matching."""

    type: CapabilityType | None = None
    permission: Permission | None = None
    version: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    max_results: int = 10


class CapabilityResolver:
    """Resolve a natural-language goal to registered capabilities.

    A capability matches when every metadata constraint equals a capability
    metadata value and at least one goal token appears in the capability
    name, type, or metadata values. Returning an empty list is a normal
    outcome -- callers surface it as an explicit "capability unavailable"
    branch instead of an error.
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        goal: str,
        *,
        query: CapabilityQuery | None = None,
    ) -> list[Capability]:
        query = query or CapabilityQuery()
        tokens = _tokenize(goal)
        matched_names: list[str] = []
        for capability in self._registry.list(
            type=query.type,
            permission=query.permission,
            enabled=True,
        ):
            if not self._metadata_matches(capability, query.metadata):
                continue
            if tokens and not (tokens & self._capability_tokens(capability)):
                continue
            if capability.name not in matched_names:
                matched_names.append(capability.name)
            if len(matched_names) >= query.max_results:
                break
        resolved: list[Capability] = []
        for name in matched_names:
            try:
                resolved.append(self._registry.get(name, version=query.version))
            except KeyError:
                continue
        return resolved

    @staticmethod
    def _metadata_matches(
        capability: Capability,
        constraints: tuple[tuple[str, str], ...]
    ) -> bool:
        for key, expected in constraints:
            actual = capability.metadata.get(key)
            if actual is None:
                return False
            values = actual if isinstance(actual, list) else [actual]
            if expected.lower() not in {str(value).lower() for value in values}:
                return False
        return True

    @staticmethod
    def _capability_tokens(capability: Capability) -> frozenset[str]:
        haystack = " ".join(
            [
                capability.name.replace("_", " "),
                capability.type.value,
                " ".join(capability.metadata.keys()),
            ]
        )
        for value in capability.metadata.values():
            values = value if isinstance(value, list) else [value]
            haystack += " " + " ".join(str(item) for item in values)
        return _tokenize(haystack)


__all__ = ["CapabilityQuery", "CapabilityResolver"]
