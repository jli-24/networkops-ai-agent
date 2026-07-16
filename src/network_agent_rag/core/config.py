"""Application settings."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = "network-agent-rag"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    checkpoint_db_path: str = "data/enterprise/checkpoints.sqlite3"
    audit_db_path: str = "data/enterprise/audit.sqlite3"
    observability_db_path: str = "data/enterprise/observability.sqlite3"
    benchmark_results_path: str = "data/evaluations"
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    checkpoint_backend: Literal["sqlite", "postgres", "redis"] = "sqlite"
    database_url: str | None = None
    redis_url: str | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=30, gt=0)
    approval_ttl_seconds: int = 1800
    langgraph_strict_msgpack: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
