"""Version-aware registry mapping capabilities to callable handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from network_agent_rag.capability.models import Capability, CapabilityType
from network_agent_rag.auth import Permission


Handler = Callable[..., Any]


def _version_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class CapabilityRegistry:
    """Deterministic, append-only registry of versioned capabilities.

    The same capability name may carry multiple versions (``esp32_compile``
    @1.0 Arduino vs @2.0 ESP-IDF); ``get`` picks the highest enabled version
    unless an explicit version is requested.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, Capability]] = {}
        self._handlers: dict[str, Handler] = {}

    def register(self, capability: Capability, handler: Handler | None = None) -> None:
        versions = self._capabilities.setdefault(capability.name, {})
        if capability.version in versions:
            raise ValueError(
                f"capability {capability.key} is already registered"
            )
        versions[capability.version] = capability
        if handler is not None:
            self._handlers[capability.key] = handler

    def get(self, name: str, *, version: str | None = None) -> Capability:
        versions = self._capabilities.get(name)
        if not versions:
            raise KeyError(f"unknown capability: {name}")
        if version is not None:
            capability = versions.get(version)
            if capability is None:
                raise KeyError(f"unknown capability: {name}@{version}")
            if not capability.enabled:
                raise KeyError(f"capability disabled: {capability.key}")
            return capability
        enabled = [item for item in versions.values() if item.enabled]
        if not enabled:
            raise KeyError(f"capability has no enabled version: {name}")
        return max(enabled, key=lambda item: _version_sort_key(item.version))

    def handler(self, name: str, *, version: str | None = None) -> Handler:
        capability = self.get(name, version=version)
        handler = self._handlers.get(capability.key)
        if handler is None:
            raise KeyError(f"capability has no handler: {capability.key}")
        return handler

    def list(
        self,
        *,
        type: CapabilityType | None = None,
        permission: Permission | None = None,
        enabled: bool | None = None,
    ) -> list[Capability]:
        capabilities = [
            capability
            for versions in self._capabilities.values()
            for capability in versions.values()
        ]
        if type is not None:
            capabilities = [item for item in capabilities if item.type == type]
        if permission is not None:
            capabilities = [item for item in capabilities if item.permission == permission]
        if enabled is not None:
            capabilities = [item for item in capabilities if item.enabled == enabled]
        return sorted(capabilities, key=lambda item: (item.name, _version_sort_key(item.version)))


__all__ = ["CapabilityRegistry", "Handler"]
