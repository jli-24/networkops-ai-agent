"""Pack registry: fail-fast registration pipeline for domain packs.

Registration order is the validation order (RFC v0.16 §3)::

    register(spec)
      1. schema validation            (Pydantic, at spec construction)
      2. (name, version) conflicts    (duplicates and same-name re-register)
      3. depends_on existence
      4. governance consistency:
         a. permission catalog boundary (unknown permission -> fail-fast)
         b. role references within pack ∪ platform permissions
         c. capability.permission within pack ∪ platform permissions
         d. PolicyEngine alignment (see _validate_policy_consistency)
      5. factory import-path resolution (module:attr, callable check)
      6. full registration (capabilities -> CapabilityRegistry)
      7. enable (lifecycle.status; API mounting consumes this in later steps)

Risk-window note (v0.15 carry-over item ①): v0.15.0 shipped capabilities
and policy configuration without a registration-time consistency gate, so
drift between them was possible. This registry closes that gap; the
consistency test in tests/test_pack_registry.py is its executable proof.
"""

from __future__ import annotations

import importlib
from typing import Mapping

from network_agent_rag.auth.models import Permission
from network_agent_rag.capability import CapabilityRegistry
from network_agent_rag.packs.models import DomainPackSpec
from network_agent_rag.policy.engine import PolicyEngine
from network_agent_rag.policy.models import PolicyOperation


class PackRegistrationError(ValueError):
    """Raised when a pack fails any registration gate."""


def _default_platform_permissions() -> frozenset[str]:
    return frozenset(permission.value for permission in Permission)


def _resolve_callable(import_path: str) -> None:
    module_name, _, attribute = import_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise PackRegistrationError(
            f"factory module not importable: {module_name!r} ({error})"
        ) from error
    target = getattr(module, attribute, None)
    if not callable(target):
        raise PackRegistrationError(
            f"factory attribute is not callable: {import_path!r}"
        )


class PackRegistry:
    """Process-local pack registry (the local Installer arrives in v0.20)."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        platform_permissions: frozenset[str] | None = None,
    ) -> None:
        self._packs: dict[str, DomainPackSpec] = {}
        self._capability_registry = capability_registry or CapabilityRegistry()
        self._policy_engine = policy_engine
        self._platform_permissions = (
            platform_permissions
            if platform_permissions is not None
            else _default_platform_permissions()
        )

    @property
    def capability_registry(self) -> CapabilityRegistry:
        return self._capability_registry

    @property
    def platform_permissions(self) -> frozenset[str]:
        return self._platform_permissions

    def register(self, spec: DomainPackSpec) -> None:
        self._validate_no_conflict(spec)
        self._validate_dependencies(spec)
        self._validate_governance(spec)
        self._validate_factories(spec)
        self._register_capabilities(spec)
        self._packs[spec.name] = spec

    def get(self, name: str) -> DomainPackSpec:
        pack = self._packs.get(name)
        if pack is None:
            raise KeyError(f"unknown pack: {name}")
        return pack

    def list(self, *, enabled: bool | None = None) -> list[DomainPackSpec]:
        packs = list(self._packs.values())
        if enabled is not None:
            wanted = "enabled" if enabled else "disabled"
            packs = [
                pack
                for pack in packs
                if pack.lifecycle.status.value == wanted
            ]
        return sorted(packs, key=lambda pack: pack.name)

    def _validate_no_conflict(self, spec: DomainPackSpec) -> None:
        existing = self._packs.get(spec.name)
        if existing is None:
            return
        if existing.version == spec.version:
            raise PackRegistrationError(
                f"pack already registered: {spec.name}@{spec.version}"
            )
        raise PackRegistrationError(
            "registering multiple versions of a pack is not supported "
            f"before v0.20 (have {existing.version}, got {spec.version} "
            f"for {spec.name})"
        )

    def _validate_dependencies(self, spec: DomainPackSpec) -> None:
        for dependency in spec.depends_on:
            if dependency not in self._packs:
                raise PackRegistrationError(
                    f"pack {spec.name} depends on unregistered pack: {dependency}"
                )

    def _validate_governance(self, spec: DomainPackSpec) -> None:
        declared = spec.permission_names
        for permission in spec.permissions:
            if permission.name not in self._platform_permissions:
                raise PackRegistrationError(
                    f"unknown permission (not in platform catalog): "
                    f"{permission.name!r} in pack {spec.name}"
                )
        for role in spec.roles:
            unknown = [
                name
                for name in role.permissions
                if name not in declared and name not in self._platform_permissions
            ]
            if unknown:
                raise PackRegistrationError(
                    f"role {role.name!r} references permissions outside "
                    f"pack {spec.name} and the platform catalog: {unknown}"
                )
        for capability in spec.capabilities:
            required = capability.permission.value
            if required not in declared and required not in self._platform_permissions:
                raise PackRegistrationError(
                    f"capability {capability.key} requires permission "
                    f"{required!r} which is neither declared by pack "
                    f"{spec.name} nor platform-provided"
                )
        if self._policy_engine is not None:
            self._validate_policy_consistency(spec)

    def _validate_policy_consistency(self, spec: DomainPackSpec) -> None:
        engine = self._policy_engine
        tool_operations: Mapping[str, PolicyOperation] = (
            engine.registry.tool_operations
        )
        rules = engine.registry.rules
        for capability in spec.capabilities:
            operation = tool_operations.get(capability.name)
            if operation is None or operation == PolicyOperation.UNKNOWN:
                continue  # unclassified tools are not policy-gated
            covered = any(
                (
                    rule.condition.operation is None
                    or operation in rule.condition.operation
                )
                and (
                    rule.condition.permission is None
                    or capability.permission in rule.condition.permission
                )
                for rule in rules
            )
            if not covered:
                raise PackRegistrationError(
                    f"capability {capability.key} (permission "
                    f"{capability.permission.value}, operation "
                    f"{operation.value}) is not covered by any policy rule; "
                    "registering it would leave the tool permanently denied"
                )

    def _validate_factories(self, spec: DomainPackSpec) -> None:
        for factory in (
            *spec.agent_factories,
            *spec.workflow_factories,
            *(router.factory for router in spec.api_routers),
        ):
            _resolve_callable(factory)

    def _register_capabilities(self, spec: DomainPackSpec) -> None:
        for capability in spec.capabilities:
            try:
                self._capability_registry.register(capability)
            except ValueError as error:
                raise PackRegistrationError(
                    f"capability registration failed for {capability.key}: {error}"
                ) from error


__all__ = ["PackRegistrationError", "PackRegistry"]
