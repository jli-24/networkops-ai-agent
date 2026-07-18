"""Production deployment configuration and adapter tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import importlib
import importlib.metadata
import json
import os
import tomllib
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from network_agent_rag.audit import AuditEventType, SQLiteAuditLog
from network_agent_rag.core.config import Settings
from network_agent_rag.observability import SQLiteTraceStore
from network_agent_rag.observability.deployment import (
    DeploymentMetricsMiddleware,
    RequestMetrics,
)
import network_agent_rag


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentSettingsTests(unittest.TestCase):
    def test_release_version_is_consistent(self) -> None:
        project = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(project["project"]["version"], "0.13.0")
        self.assertEqual(network_agent_rag.__version__, "0.13.0")
        self.assertEqual(importlib.metadata.version("networkops-ai-agent"), "0.13.0")

    def test_app_env_is_primary_and_environment_remains_compatible(self) -> None:
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "ENVIRONMENT": "development"},
            clear=False,
        ):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.environment, "production")

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=True):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.environment, "production")

    def test_development_defaults_remain_sqlite_and_metrics_disabled(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.storage_backend, "sqlite")
        self.assertEqual(settings.checkpoint_backend, "sqlite")
        self.assertFalse(settings.prometheus_enabled)
        self.assertTrue(settings.policy_engine_enabled)
        self.assertIsNone(settings.networkops_workflow_factory)


class DeploymentFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("deployment.app")

    def _production_settings(self, **updates: object) -> Settings:
        values: dict[str, object] = {
            "environment": "production",
            "storage_backend": "postgres",
            "checkpoint_backend": "redis",
            "database_url": "postgresql://user:secret@postgres/networkops",
            "redis_url": "redis://:secret@redis/0",
            "identity_redis_url": "redis://:secret@redis/1",
            "jwt_secret_key": "x" * 32,
            "networkops_workflow_factory": "example.factory:create_workflow",
            "prometheus_enabled": True,
        }
        values.update(updates)
        return Settings(_env_file=None, **values)

    def test_production_requires_explicit_backends_and_secrets(self) -> None:
        invalid = (
            {"storage_backend": "sqlite"},
            {"checkpoint_backend": "sqlite"},
            {"database_url": None},
            {"redis_url": None},
            {"identity_redis_url": None},
            {"identity_redis_url": "redis://:secret@redis/0"},
            {"jwt_secret_key": None},
            {"networkops_workflow_factory": None},
            {"policy_engine_enabled": False},
        )
        for update in invalid:
            with self.subTest(update=next(iter(update))):
                with self.assertRaisesRegex(RuntimeError, "production deployment"):
                    self.module.validate_production_settings(
                        self._production_settings(**update)
                    )

    def test_factory_loader_accepts_only_module_callable_paths(self) -> None:
        expected = lambda checkpointer, **dependencies: object()
        fake_module = type("FactoryModule", (), {})()
        fake_module.build = expected
        with patch.object(self.module.importlib, "import_module", return_value=fake_module):
            self.assertIs(self.module.load_workflow_factory("site.factory:build"), expected)

        for value in ("", "site.factory", "site.factory:"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "workflow factory"):
                    self.module.load_workflow_factory(value)

        fake_module = type("FactoryModule", (), {"build": 42})()
        with patch.object(self.module.importlib, "import_module", return_value=fake_module):
            with self.assertRaisesRegex(RuntimeError, "workflow factory"):
                self.module.load_workflow_factory("site.factory:build")

    def test_factory_errors_do_not_expose_connection_secrets(self) -> None:
        settings = self._production_settings()
        with patch.object(
            self.module,
            "load_workflow_factory",
            side_effect=RuntimeError("workflow factory cannot be loaded"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.module.create_app(settings=settings)
        message = str(raised.exception)
        self.assertNotIn("postgresql://", message)
        self.assertNotIn("redis://", message)
        self.assertNotIn("secret", message)

    def test_create_app_reuses_storage_factory(self) -> None:
        settings = self._production_settings()
        workflow_factory = lambda saver, **dependencies: object()
        application = FastAPI()
        with (
            patch.object(
                self.module,
                "load_workflow_factory",
                return_value=workflow_factory,
            ),
            patch.object(
                self.module,
                "create_storage_enterprise_app",
                return_value=application,
            ) as storage_factory,
        ):
            result = self.module.create_app(settings=settings)
        self.assertIs(result, application)
        storage_factory.assert_called_once()
        arguments = storage_factory.call_args.kwargs
        self.assertIn("policy_workflow_factory", arguments)
        self.assertEqual(arguments["storage_backend"], "postgres")
        self.assertEqual(arguments["checkpoint_backend"], "redis")
        self.assertEqual(arguments["database_url"], settings.database_url)
        self.assertEqual(arguments["redis_url"], settings.redis_url)
        self.assertEqual(arguments["identity_redis_url"], settings.identity_redis_url)
        manager = arguments["identity_token_manager"]
        self.assertEqual(manager.access_expire_minutes, 15)
        self.assertEqual(manager.refresh_expire_days, 7)


class DeploymentHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("deployment.app")

    def _app(self, database_ok: bool, redis_ok: bool) -> FastAPI:
        application = FastAPI()
        settings = Settings(
            _env_file=None,
            environment="production",
            storage_backend="postgres",
            checkpoint_backend="redis",
            database_url="postgresql://redacted",
            redis_url="redis://redacted",
            identity_redis_url="redis://identity-redacted/1",
            jwt_secret_key="x" * 32,
            networkops_workflow_factory="site.factory:build",
        )
        self.module.install_deployment_features(
            application,
            settings,
            database_probe=lambda _url: database_ok,
            redis_probe=lambda _url: redis_ok,
        )
        return application

    def test_readiness_reports_dependencies_without_details(self) -> None:
        response = TestClient(self._app(True, True)).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"service": "ok", "database": "ok", "redis": "ok"},
        )

    def test_readiness_returns_503_for_unavailable_dependency(self) -> None:
        response = TestClient(self._app(False, True)).get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "unavailable")
        self.assertNotIn("postgresql", response.text)

    def test_development_reports_unused_redis_as_not_configured(self) -> None:
        application = FastAPI()
        settings = Settings(_env_file=None)
        self.module.install_deployment_features(application, settings)
        response = TestClient(application).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "ok")
        self.assertEqual(response.json()["redis"], "not_configured")

    def test_legacy_health_contract_is_unchanged(self) -> None:
        from network_agent_rag.main import create_app

        application = create_app()
        self.module.install_deployment_features(application, Settings(_env_file=None))
        client = TestClient(application)
        self.assertEqual(
            client.get("/api/v1/health").json(),
            {"status": "ok"},
        )
        self.assertEqual(client.get("/health").json()["service"], "ok")


class DeploymentMetricsTests(unittest.TestCase):
    def test_middleware_combines_http_agent_and_authorization_metrics(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            now = datetime.now(timezone.utc)
            span = trace.start_span(
                trace_id="trace-1",
                run_id="run-1",
                incident_id="INC-1",
                kind="workflow",
                name="EnterpriseWorkflow",
                started_at=now,
            )
            trace.finish_span(span.span_id, status="succeeded", ended_at=now)
            audit.record(
                incident_id="INC-1",
                event_type=AuditEventType.DECISION,
                actor="api_authorization",
                action="authorize_api_execution",
                outcome="denied",
                details={
                    "actor_id": "operator-1",
                    "actor_role": "Operator",
                    "permission": "EXECUTE_REPAIR",
                },
            )

            application = FastAPI()
            application.state.trace_store = trace
            application.state.audit_log = audit

            @application.get("/ok")
            async def ok() -> dict[str, str]:
                return {"status": "ok"}

            @application.get("/failed")
            async def failed() -> Response:
                return Response(status_code=500)

            metrics = RequestMetrics()
            application.add_middleware(
                DeploymentMetricsMiddleware,
                enabled=True,
                registry=metrics,
            )
            client = TestClient(application)
            self.assertEqual(client.get("/ok").status_code, 200)
            self.assertEqual(client.get("/failed").status_code, 500)
            response = client.get("/metrics")
            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertIn("networkops_incidents_total", body)
            self.assertIn("networkops_http_requests_total", body)
            self.assertIn("networkops_http_request_duration_seconds_sum", body)
            self.assertIn("networkops_http_request_errors_total", body)
            self.assertIn("networkops_http_request_error_rate", body)
            self.assertIn(
                'networkops_authorization_total{decision="denied",permission="EXECUTE_REPAIR"} 1',
                body,
            )
            self.assertNotIn("operator-1", body)

    def test_disabled_prometheus_returns_404(self) -> None:
        application = FastAPI()
        application.add_middleware(
            DeploymentMetricsMiddleware,
            enabled=False,
            registry=RequestMetrics(),
        )
        self.assertEqual(TestClient(application).get("/metrics").status_code, 404)


class DeploymentFilesTests(unittest.TestCase):
    def test_container_and_compose_files_define_production_boundaries(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("python:3.11", dockerfile)
        self.assertIn("USER networkops", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("networkops_ai_agent-0.13.0-py3-none-any.whl", dockerfile)
        self.assertNotIn("--reload", dockerfile)
        for service in (
            "nginx:",
            "network-agent-api:",
            "postgres:",
            "redis:",
            "prometheus:",
            "grafana:",
        ):
            self.assertIn(service, compose)
        self.assertNotIn("POSTGRES_PASSWORD:-networkops", compose)
        self.assertNotIn("REDIS_PASSWORD:-networkops", compose)

    def test_gateway_prometheus_and_grafana_configuration_is_valid_json_or_text(self) -> None:
        nginx = (PROJECT_ROOT / "deploy/nginx/nginx.conf").read_text(encoding="utf-8")
        prometheus = (PROJECT_ROOT / "deploy/prometheus/prometheus.yml").read_text(
            encoding="utf-8"
        )
        dashboard = json.loads(
            (PROJECT_ROOT / "deploy/grafana/dashboards/networkops.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("proxy_buffering off", nginx)
        self.assertIn("client_max_body_size", nginx)
        self.assertIn("location = /metrics", nginx)
        self.assertIn("network-agent-api:8000", prometheus)
        self.assertEqual(dashboard["title"], "NetworkOps AI Agent")

    def test_ci_builds_container_without_publishing(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("docker build", workflow)
        self.assertNotIn("docker push", workflow)


if __name__ == "__main__":
    unittest.main()
