"""Checkpoint backend adapter and storage application assembly tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from network_agent_rag.api.enterprise import create_storage_enterprise_app
from network_agent_rag.api.enterprise import _checkpoint_context, _storage_context
from network_agent_rag.storage.postgres import open_postgres_checkpointer
from network_agent_rag.storage.redis import open_redis_checkpointer
from network_agent_rag.storage.sqlite import open_sqlite_checkpointer


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class CheckpointerAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_adapter_sets_up_checkpointer(self) -> None:
        saver = MagicMock()
        saver.setup = AsyncMock()
        with TemporaryDirectory() as directory, patch(
            "network_agent_rag.storage.sqlite.AsyncSqliteSaver.from_conn_string",
            return_value=_AsyncContext(saver),
        ) as factory:
            path = Path(directory) / "checkpoint.sqlite3"
            async with open_sqlite_checkpointer(path) as opened:
                self.assertIs(opened, saver)

        factory.assert_called_once_with(str(path))
        saver.setup.assert_awaited_once_with()

    async def test_postgres_adapter_sets_up_official_saver(self) -> None:
        saver = MagicMock()
        saver.setup = AsyncMock()
        with patch(
            "network_agent_rag.storage.postgres.AsyncPostgresSaver.from_conn_string",
            return_value=_AsyncContext(saver),
        ) as factory:
            async with open_postgres_checkpointer("postgresql://example") as opened:
                self.assertIs(opened, saver)

        factory.assert_called_once_with("postgresql://example")
        saver.setup.assert_awaited_once_with()

    async def test_redis_adapter_is_checkpoint_only_and_sets_up_saver(self) -> None:
        saver = SimpleNamespace(asetup=AsyncMock())
        with patch(
            "network_agent_rag.storage.redis.AsyncRedisSaver.from_conn_string",
            return_value=_AsyncContext(saver),
        ) as factory:
            async with open_redis_checkpointer("redis://example") as opened:
                self.assertIs(opened, saver)

        factory.assert_called_once_with("redis://example")
        saver.asetup.assert_awaited_once_with()
        for forbidden in ("cache", "memory", "queue", "publish"):
            self.assertFalse(hasattr(opened, forbidden))


class StorageApplicationAssemblyTests(unittest.TestCase):
    def test_rejects_invalid_backend_and_missing_urls_without_leaking_url(self) -> None:
        factory = lambda saver, audit: object()
        with self.assertRaisesRegex(ValueError, "storage backend"):
            create_storage_enterprise_app(
                workflow_factory=factory,
                storage_backend="invalid",
            )
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            create_storage_enterprise_app(
                workflow_factory=factory,
                storage_backend="postgres",
                database_url="",
            )
        with self.assertRaisesRegex(ValueError, "REDIS_URL"):
            create_storage_enterprise_app(
                workflow_factory=factory,
                checkpoint_backend="redis",
                redis_url="",
            )

    def test_six_storage_checkpoint_combinations_select_expected_adapters(self) -> None:
        combinations = [
            ("sqlite", "sqlite"),
            ("sqlite", "postgres"),
            ("sqlite", "redis"),
            ("postgres", "sqlite"),
            ("postgres", "postgres"),
            ("postgres", "redis"),
        ]

        for storage_backend, checkpoint_backend in combinations:
            with self.subTest(storage=storage_backend, checkpoint=checkpoint_backend):
                calls: list[str] = []
                audit = MagicMock()
                trace = MagicMock()
                saver = MagicMock()

                @asynccontextmanager
                async def checkpoint_context(*args, **kwargs):
                    calls.append(f"checkpoint:{checkpoint_backend}")
                    yield saver

                class StoreContext:
                    def __enter__(self):
                        calls.append(f"storage:{storage_backend}")
                        return audit, trace

                    def __exit__(self, exc_type, exc, traceback):
                        return None

                def observed_factory(checkpointer, **dependencies):
                    self.assertIs(checkpointer, saver)
                    self.assertIs(dependencies["audit_log"], audit)
                    self.assertIs(dependencies["trace_store"], trace)
                    return MagicMock()

                with TemporaryDirectory() as directory, patch(
                    "network_agent_rag.api.enterprise._storage_context",
                    return_value=StoreContext(),
                ), patch(
                    "network_agent_rag.api.enterprise._checkpoint_context",
                    side_effect=checkpoint_context,
                ):
                    app = create_storage_enterprise_app(
                        observed_workflow_factory=observed_factory,
                        storage_backend=storage_backend,
                        checkpoint_backend=checkpoint_backend,
                        database_url="postgresql://hidden:secret@example/database",
                        redis_url="redis://:secret@example/0",
                        checkpoint_path=Path(directory) / "checkpoint.sqlite3",
                        audit_path=Path(directory) / "audit.sqlite3",
                        observability_path=Path(directory) / "trace.sqlite3",
                        benchmark_results_path=Path(directory) / "benchmarks",
                    )
                    with TestClient(app):
                        pass

                self.assertEqual(calls, [
                    f"storage:{storage_backend}",
                    f"checkpoint:{checkpoint_backend}",
                ])

    def test_storage_setup_failure_does_not_expose_connection_url(self) -> None:
        secret_url = "postgresql://user:top-secret@example/database"
        with patch(
            "network_agent_rag.api.enterprise.open_postgres_stores",
            side_effect=RuntimeError(secret_url),
        ), self.assertRaises(RuntimeError) as raised:
            with _storage_context(
                "postgres",
                audit_path=Path("audit.sqlite3"),
                trace_path=Path("trace.sqlite3"),
                database_url=secret_url,
            ):
                pass

        self.assertNotIn("top-secret", str(raised.exception))


class CheckpointFailureSanitizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_setup_failure_does_not_expose_connection_url(self) -> None:
        secret_url = "redis://:top-secret@example/0"

        @asynccontextmanager
        async def failing_context(*args, **kwargs):
            raise RuntimeError(secret_url)
            yield

        with patch(
            "network_agent_rag.api.enterprise.open_redis_checkpointer",
            side_effect=failing_context,
        ), self.assertRaises(RuntimeError) as raised:
            async with _checkpoint_context(
                "redis",
                sqlite_path=Path("checkpoint.sqlite3"),
                database_url=None,
                redis_url=secret_url,
            ):
                pass

        self.assertNotIn("top-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
