"""DomainPack contract and PackRegistry fail-fast pipeline tests.

Coverage matrix (v0.16 acceptance items 4-7 + addenda a-b):
- governance consistency mismatch -> fail-fast
- unknown permission -> fail-fast (catalog boundary)
- (name, version) conflict -> fail-fast; capability multi-version semantics
  and resolve behavior unchanged
- lifecycle / ui_extensions placeholders: schema-accepted, runtime-ignored
- full registration order incl. enable + resolve over registered assets
- role over-reach, unimportable factories, missing dependencies
"""

from __future__ import annotations

import unittest

from network_agent_rag.auth import Permission
from network_agent_rag.capability import (
    Capability,
    CapabilityRegistry,
    CapabilityResolver,
    CapabilityType,
)
from network_agent_rag.packs import (
    DomainPackSpec,
    LifecycleSpec,
    LifecycleStatus,
    PackRegistrationError,
    PackRegistry,
    PermissionSpec,
    RoleSpec,
    RouterSpec,
    UiExtensionSpec,
)
from network_agent_rag.policy.engine import PolicyEngine
from network_agent_rag.policy.models import (
    PolicyCondition,
    PolicyEffect,
    PolicyOperation,
    PolicyRule,
)
from network_agent_rag.policy.registry import PolicyRegistry


_FACTORY = "network_agent_rag.packs.embeddedops.manifest:register_embeddedops_pack"


def _capability(
    name: str = "esp32_compile",
    version: str = "1.0.0",
    permission: Permission = Permission.EMBEDDED_SIMULATE,
) -> Capability:
    return Capability(
        name=name,
        version=version,
        type=CapabilityType.FIRMWARE,
        permission=permission,
        backend="in_process",
    )


def _spec(
    *,
    name: str = "embeddedops",
    version: str = "0.15.0",
    capabilities: tuple[Capability, ...] | None = None,
    permissions: tuple[PermissionSpec, ...] | None = None,
    roles: tuple[RoleSpec, ...] | None = None,
    lifecycle: LifecycleSpec | None = None,
    ui_extensions: tuple[UiExtensionSpec, ...] = (),
    depends_on: tuple[str, ...] = (),
    api_routers: tuple[RouterSpec, ...] = (),
    agent_factories: tuple[str, ...] = (),
) -> DomainPackSpec:
    return DomainPackSpec(
        name=name,
        version=version,
        description="test pack",
        lifecycle=lifecycle or LifecycleSpec(),
        capabilities=(
            capabilities
            if capabilities is not None
            else (_capability(),)
        ),
        agent_factories=agent_factories,
        permissions=(
            permissions
            if permissions is not None
            else (PermissionSpec(name=Permission.EMBEDDED_SIMULATE.value),)
        ),
        roles=(
            roles
            if roles is not None
            else (
                RoleSpec(
                    name="EmbeddedEngineer",
                    permissions=(Permission.EMBEDDED_SIMULATE.value,),
                ),
            )
        ),
        ui_extensions=ui_extensions,
        depends_on=depends_on,
        api_routers=api_routers,
    )


def _policy_engine(
    rules: tuple[PolicyRule, ...],
    tool_operations: dict[str, PolicyOperation],
) -> PolicyEngine:
    return PolicyEngine(
        PolicyRegistry(rules=rules, tool_operations=tool_operations)
    )


class PackContractSchemaTests(unittest.TestCase):
    def test_governance_assets_are_mandatory(self) -> None:
        with self.assertRaises(ValueError):
            _spec(permissions=())
        with self.assertRaises(ValueError):
            _spec(roles=())

    def test_name_and_version_formats(self) -> None:
        with self.assertRaises(ValueError):
            _spec(name="EmbeddedOps")
        with self.assertRaises(ValueError):
            _spec(version="1.0")

    def test_import_paths_must_be_module_colon_attr(self) -> None:
        with self.assertRaises(ValueError):
            _spec(agent_factories=("not.a.path",))
        with self.assertRaises(ValueError):
            _spec(api_routers=(RouterSpec(prefix="/x", factory="no.attr.here"),))

    def test_no_duplicate_permissions_roles_or_self_dependency(self) -> None:
        with self.assertRaises(ValueError):
            _spec(
                permissions=(
                    PermissionSpec(name=Permission.EMBEDDED_SIMULATE.value),
                    PermissionSpec(name=Permission.EMBEDDED_SIMULATE.value),
                )
            )
        with self.assertRaises(ValueError):
            _spec(depends_on=("embeddedops",))


class PackRegistrationPipelineTests(unittest.TestCase):
    def test_full_registration_flow_enables_and_resolves_assets(self) -> None:
        registry = PackRegistry()
        registry.register(_spec(agent_factories=(_FACTORY,)))

        self.assertEqual(
            [pack.name for pack in registry.list()], ["embeddedops"]
        )
        self.assertEqual(registry.list(enabled=True)[0].name, "embeddedops")
        resolver = CapabilityResolver(registry.capability_registry)
        resolved = resolver.resolve("esp32 compile firmware")
        self.assertEqual([item.key for item in resolved], ["esp32_compile@1.0.0"])

    def test_name_version_conflict_fails_fast(self) -> None:
        registry = PackRegistry()
        registry.register(_spec())
        with self.assertRaises(PackRegistrationError):
            registry.register(_spec())

    def test_same_name_different_version_rejected_before_v020(self) -> None:
        registry = PackRegistry()
        registry.register(_spec())
        with self.assertRaises(PackRegistrationError):
            registry.register(_spec(version="0.16.0"))

    def test_capability_multi_version_and_resolve_default_keep_semantics(
        self,
    ) -> None:
        registry = PackRegistry()
        registry.register(
            _spec(capabilities=(_capability(version="1.0.0"),), name="packa")
        )
        registry.register(
            _spec(
                capabilities=(
                    _capability(version="2.0.0"),
                    _capability(version="1.5.0"),
                ),
                name="packb",
            )
        )
        # CapabilityRegistry keeps coexisting versions and resolves highest.
        self.assertEqual(
            registry.capability_registry.get("esp32_compile").version, "2.0.0"
        )

    def test_unknown_permission_fails_fast(self) -> None:
        registry = PackRegistry()
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(
                _spec(
                    permissions=(PermissionSpec(name="TELEPORT_DEVICE"),),
                    roles=(
                        RoleSpec(
                            name="Wizard", permissions=("TELEPORT_DEVICE",)
                        ),
                    ),
                    capabilities=(),
                )
            )
        self.assertIn("unknown permission", str(context.exception))

    def test_role_referencing_undeclared_permission_fails_fast(self) -> None:
        # Restricted catalog makes MANAGE_SYSTEM neither declared nor platform.
        registry = PackRegistry(
            platform_permissions=frozenset({Permission.EMBEDDED_SIMULATE.value})
        )
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(
                _spec(
                    roles=(
                        RoleSpec(
                            name="Greedy",
                            permissions=(Permission.MANAGE_SYSTEM.value,),
                        ),
                    )
                )
            )
        self.assertIn("outside pack", str(context.exception))

    def test_capability_permission_not_declared_fails_fast(self) -> None:
        # Catalog holds EMBEDDED_READ only; the pack declares EMBEDDED_READ
        # but its capability needs EMBEDDED_SIMULATE -> gate (c) fires.
        registry = PackRegistry(
            platform_permissions=frozenset({Permission.EMBEDDED_READ.value})
        )
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(
                _spec(
                    permissions=(
                        PermissionSpec(name=Permission.EMBEDDED_READ.value),
                    ),
                    roles=(
                        RoleSpec(
                            name="Reader",
                            permissions=(Permission.EMBEDDED_READ.value,),
                        ),
                    ),
                )
            )
        self.assertIn("requires permission", str(context.exception))

    def test_missing_dependency_fails_fast(self) -> None:
        registry = PackRegistry()
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(_spec(depends_on=("ghostpack",)))
        self.assertIn("unregistered pack", str(context.exception))

    def test_unimportable_factory_fails_fast(self) -> None:
        registry = PackRegistry()
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(_spec(agent_factories=("no.such.module:entry",)))
        self.assertIn("not importable", str(context.exception))

    def test_non_callable_factory_fails_fast(self) -> None:
        registry = PackRegistry()
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(
                _spec(
                    agent_factories=(
                        "network_agent_rag.packs:PackRegistry_NOT_A_ATTR",
                    )
                )
            )
        self.assertIn("not callable", str(context.exception))

    def test_partial_registration_leaves_no_trace(self) -> None:
        registry = PackRegistry()
        with self.assertRaises(PackRegistrationError):
            registry.register(_spec(depends_on=("ghostpack",)))
        self.assertEqual(registry.list(), [])
        self.assertEqual(registry.capability_registry.list(), [])


class PlaceholderFieldTests(unittest.TestCase):
    """Acceptance item 7: schema-accepted, runtime-ignored — both tested."""

    def test_ui_extensions_accepted_and_stored_but_have_no_runtime_effect(
        self,
    ) -> None:
        registry = PackRegistry()
        registry.register(
            _spec(
                ui_extensions=(
                    UiExtensionSpec(view_id="embedded", label="Embedded Lab", glyph="⬡"),
                )
            )
        )
        stored = registry.get("embeddedops")
        self.assertEqual(stored.ui_extensions[0].view_id, "embedded")
        # Runtime effect is explicitly absent: capabilities unchanged.
        self.assertEqual(
            [item.name for item in registry.capability_registry.list()],
            ["esp32_compile"],
        )

    def test_lifecycle_disabled_pack_registered_but_not_enabled(self) -> None:
        registry = PackRegistry()
        registry.register(
            _spec(lifecycle=LifecycleSpec(status=LifecycleStatus.DISABLED))
        )
        self.assertEqual(registry.list(enabled=True), [])
        self.assertEqual(
            [pack.name for pack in registry.list(enabled=False)], ["embeddedops"]
        )


class PolicyConsistencyTests(unittest.TestCase):
    """v0.15 carry-over item (1): Registry<->PolicyEngine alignment gate."""

    def _rule(
        self,
        *,
        permission: frozenset[Permission] | None,
        operation: frozenset[PolicyOperation] | None = None,
    ) -> PolicyRule:
        return PolicyRule(
            rule_id="rule-1",
            name="gate",
            condition=PolicyCondition(permission=permission, operation=operation),
            effect=PolicyEffect.ALLOW,
        )

    def test_capability_without_policy_coverage_fails_fast(self) -> None:
        engine = _policy_engine(
            rules=(
                self._rule(
                    permission=frozenset({Permission.EMBEDDED_READ}),
                ),
            ),
            tool_operations={"esp32_compile": PolicyOperation.RESTART_DEVICE},
        )
        registry = PackRegistry(policy_engine=engine)
        with self.assertRaises(PackRegistrationError) as context:
            registry.register(_spec())
        self.assertIn("not covered by any policy rule", str(context.exception))

    def test_covered_capability_registers(self) -> None:
        engine = _policy_engine(
            rules=(
                # operation-scoped, permission-unconstrained allow rule
                self._rule(
                    permission=None,
                    operation=frozenset({PolicyOperation.RESTART_DEVICE}),
                ),
            ),
            tool_operations={"esp32_compile": PolicyOperation.RESTART_DEVICE},
        )
        registry = PackRegistry(policy_engine=engine)
        registry.register(_spec())
        self.assertEqual(len(registry.list()), 1)

    def test_unclassified_tool_is_not_policy_gated(self) -> None:
        engine = _policy_engine(
            rules=(),
            tool_operations={},
        )
        registry = PackRegistry(policy_engine=engine)
        registry.register(_spec())
        self.assertEqual(len(registry.list()), 1)

    def test_operation_conflicting_rules_do_not_cover(self) -> None:
        engine = _policy_engine(
            rules=(
                self._rule(
                    permission=None,
                    operation=frozenset({PolicyOperation.VIEW_DEVICE_STATUS}),
                ),
            ),
            tool_operations={"esp32_compile": PolicyOperation.RESTART_DEVICE},
        )
        registry = PackRegistry(policy_engine=engine)
        with self.assertRaises(PackRegistrationError):
            registry.register(_spec())


if __name__ == "__main__":
    unittest.main()
