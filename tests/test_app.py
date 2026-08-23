"""Smoke tests for the application skeleton."""

from __future__ import annotations

import asyncio
import importlib.util
from importlib import import_module
import unittest

from fastapi import FastAPI


class PackageStructureTests(unittest.TestCase):
    def test_application_package_is_importable(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("network_agent_rag"))

    def test_module_boundaries_are_importable(self) -> None:
        modules = (
            "api",
            "core",
            "domain",
            "packs",
            "rag",
        )

        for module in modules:
            with self.subTest(module=module):
                qualified_name = f"network_agent_rag.{module}"
                self.assertIsNotNone(importlib.util.find_spec(qualified_name))

    def test_application_modules_are_importable(self) -> None:
        modules = (
            "network_agent_rag.packs.networkops.api.router",
            "network_agent_rag.core.config",
            "network_agent_rag.main",
        )

        for module in modules:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.util.find_spec(module))


class SettingsTests(unittest.TestCase):
    def test_settings_expose_required_defaults(self) -> None:
        module = import_module("network_agent_rag.core.config")
        settings_type = getattr(module, "Settings", None)

        self.assertIsNotNone(settings_type)
        if settings_type is None:
            return

        settings = settings_type(_env_file=None)
        self.assertEqual(settings.app_name, "network-agent-rag")
        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.api_prefix, "/api/v1")


class ApplicationTests(unittest.TestCase):
    def test_application_factory_returns_fastapi_instance(self) -> None:
        module = import_module("network_agent_rag.main")
        create_app = getattr(module, "create_app", None)

        self.assertIsNotNone(create_app)
        if create_app is None:
            return

        application = create_app()
        self.assertIsInstance(application, FastAPI)
        self.assertEqual(application.title, "network-agent-rag")
        self.assertIsInstance(module.app, FastAPI)

    def test_health_endpoint_reports_service_status(self) -> None:
        module = import_module("network_agent_rag.main")
        application = module.create_app()

        self.assertIn("/api/v1/health", application.openapi()["paths"])
        self.assertEqual(asyncio.run(module.health()), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
