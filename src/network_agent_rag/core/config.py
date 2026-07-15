"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = "network-agent-rag"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    checkpoint_db_path: str = "data/enterprise/checkpoints.sqlite3"
    audit_db_path: str = "data/enterprise/audit.sqlite3"
    approval_ttl_seconds: int = 1800
    langgraph_strict_msgpack: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
