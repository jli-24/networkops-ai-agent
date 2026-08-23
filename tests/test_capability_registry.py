"""Capability registry, versioning, and goal resolution tests."""

from __future__ import annotations

import unittest

from network_agent_rag.auth import Permission
from network_agent_rag.capability import (
    Capability,
    CapabilityQuery,
    CapabilityRegistry,
    CapabilityResolver,
    CapabilityType,
)


def _firmware_compile(version: str, framework: str) -> Capability:
    return Capability(
        name="esp32_compile",
        version=version,
        type=CapabilityType.FIRMWARE,
        input_schema={"source": "string"},
        output_schema={"binary": "string"},
        permission=Permission.EMBEDDED_SIMULATE,
        backend="in_process",
        metadata={"framework": framework, "language": "C", "mcu": ["ESP32-S3"]},
    )


class CapabilityModelTests(unittest.TestCase):
    def test_rejects_invalid_version_strings(self) -> None:
        with self.assertRaises(ValueError):
            _firmware_compile("v2", "Arduino")

    def test_key_combines_name_and_version(self) -> None:
        capability = _firmware_compile("2.0", "ESP-IDF")
        self.assertEqual(capability.key, "esp32_compile@2.0")


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CapabilityRegistry()
        self.arduino = _firmware_compile("1.0", "Arduino")
        self.idf = _firmware_compile("2.0", "ESP-IDF")
        self.registry.register(self.arduino, handler=lambda source: f"arduino:{source}")
        self.registry.register(self.idf, handler=lambda source: f"idf:{source}")

    def test_get_defaults_to_latest_enabled_version(self) -> None:
        self.assertEqual(self.registry.get("esp32_compile"), self.idf)

    def test_get_selects_exact_version(self) -> None:
        self.assertEqual(
            self.registry.get("esp32_compile", version="1.0"), self.arduino
        )

    def test_get_skips_disabled_versions(self) -> None:
        self.registry.register(
            Capability(
                name="esp32_compile",
                version="3.0",
                type=CapabilityType.FIRMWARE,
                permission=Permission.EMBEDDED_SIMULATE,
                enabled=False,
            )
        )
        self.assertEqual(self.registry.get("esp32_compile"), self.idf)

    def test_register_rejects_duplicate_versions(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.register(self.arduino)

    def test_unknown_capability_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.registry.get("does_not_exist")

    def test_handler_bound_to_version(self) -> None:
        handler = self.registry.handler("esp32_compile", version="1.0")
        self.assertEqual(handler("main.c"), "arduino:main.c")

    def test_list_filters_by_type_and_permission(self) -> None:
        self.registry.register(
            Capability(
                name="mcu_catalog_lookup",
                version="1.0",
                type=CapabilityType.KNOWLEDGE,
                permission=Permission.EMBEDDED_READ,
            )
        )
        firmware = self.registry.list(type=CapabilityType.FIRMWARE)
        self.assertEqual({item.name for item in firmware}, {"esp32_compile"})
        read_only = self.registry.list(permission=Permission.EMBEDDED_READ)
        self.assertEqual({item.name for item in read_only}, {"mcu_catalog_lookup"})


class CapabilityResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(_firmware_compile("1.0", "Arduino"))
        self.registry.register(_firmware_compile("2.0", "ESP-IDF"))
        self.registry.register(
            Capability(
                name="mcu_catalog_lookup",
                version="1.0",
                type=CapabilityType.KNOWLEDGE,
                permission=Permission.EMBEDDED_READ,
                metadata={"domain": "mcu"},
            )
        )
        self.resolver = CapabilityResolver(self.registry)

    def test_resolves_goal_with_metadata_filter(self) -> None:
        matches = self.resolver.resolve(
            "compile ESP32-S3 firmware",
            query=CapabilityQuery(
                type=CapabilityType.FIRMWARE,
                metadata=(("framework", "ESP-IDF"),),
            ),
        )
        self.assertEqual([item.key for item in matches], ["esp32_compile@2.0"])

    def test_resolves_exact_version(self) -> None:
        matches = self.resolver.resolve(
            "compile esp32 firmware",
            query=CapabilityQuery(version="1.0"),
        )
        self.assertEqual([item.key for item in matches], ["esp32_compile@1.0"])

    def test_unmatched_goal_returns_empty_list(self) -> None:
        self.assertEqual(self.resolver.resolve("make coffee"), [])

    def test_metadata_mismatch_excludes_capability(self) -> None:
        matches = self.resolver.resolve(
            "compile esp32 firmware",
            query=CapabilityQuery(metadata=(("framework", "Zephyr"),)),
        )
        self.assertEqual(matches, [])

    def test_list_value_metadata_matches_any_entry(self) -> None:
        matches = self.resolver.resolve(
            "esp32",
            query=CapabilityQuery(metadata=(("mcu", "esp32-s3"),)),
        )
        self.assertEqual([item.key for item in matches], ["esp32_compile@2.0"])


if __name__ == "__main__":
    unittest.main()
