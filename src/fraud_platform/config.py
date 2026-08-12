"""Centralized runtime configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_dsn: str = "postgresql://fraud:fraud@localhost:5432/fraud"
    mlflow_tracking_uri: str = "http://localhost:5001"
    mlflow_experiment_name: str = "fraud-risk-decisioning"
    mlflow_registered_model_name: str = "aegis-risk-engine"
    model_dir: Path = Path("artifacts/model")
    candidate_model_dir: Path = Path("artifacts/candidate")
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    fraud_review_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    fraud_block_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    target_fpr: float = Field(default=0.01, gt=0.0, lt=1.0)
    investigator_top_k: int = Field(default=500, gt=0)
    telemetry_window_seconds: int = Field(default=60, gt=0)
    db_connect_timeout_seconds: int = Field(default=5, gt=0)
    db_pool_timeout_seconds: float = Field(default=3.0, gt=0)
    db_statement_timeout_ms: int = Field(default=3_000, gt=0)
    label_maturity_hours: int = Field(default=168, ge=0)
    api_key: str | None = Field(default=None, validation_alias="AEGIS_API_KEY")
    enable_demo_endpoints: bool = False
    public_airflow_url: HttpUrl = HttpUrl("http://localhost:8080")
    public_mlflow_url: HttpUrl = HttpUrl("http://localhost:5001")
    public_superset_url: HttpUrl = HttpUrl(
        "http://localhost:8088/superset/dashboard/aegis-operations/"
    )
    public_api_url: HttpUrl = HttpUrl("http://localhost:8000")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached immutable-by-convention settings object."""

    return Settings()
