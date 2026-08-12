"""Validated API contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fraud_platform.features.currency import normalize_currency


class TransactionRequest(BaseModel):
    """Raw transaction attributes required for online scoring."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    customer_id: str = Field(min_length=1, max_length=128)
    merchant_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    amount: float = Field(ge=0.0, le=10_000_000.0)
    currency: str = Field(min_length=3, max_length=3)
    merchant_category: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=32)
    customer_age: int = Field(ge=18, le=120)
    account_age_days: int = Field(ge=0, le=50_000)
    distance_from_home_km: float = Field(ge=0.0, le=50_000.0)
    is_foreign: int = Field(ge=0, le=1)
    device_age_days: int = Field(ge=0, le=50_000)
    failed_attempts_24h: int = Field(ge=0, le=1_000)

    @field_validator("event_timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event_timestamp must include a timezone offset")
        return value.astimezone(UTC)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_currency(value)


class FeatureContribution(BaseModel):
    """One local SHAP contribution."""

    feature: str
    value: Any
    shap_value: float


class DecisionResponse(BaseModel):
    """Auditable scoring response."""

    prediction_id: uuid.UUID
    transaction_id: uuid.UUID
    decision: Literal["Approve", "Manually Review", "Block"]
    model_decision: Literal["Approve", "Manually Review", "Block"]
    policy_reason: str
    queue_admitted: bool
    calibrated_probability: float
    model_name: str
    model_version: str
    feature_version: str
    artifact_checksum: str
    explanation: list[FeatureContribution]
    explanation_base_value: float
    explanation_unit: Literal["probability_delta"] = "probability_delta"
    explanation_remainder: float
    correlation_id: str
    idempotent_replay: bool = False
    feature_latency_ms: float
    model_latency_ms: float
    inference_latency_ms: float


class QueueItem(BaseModel):
    """Manual-review work item returned to the investigator UI."""

    prediction_id: uuid.UUID
    transaction_id: uuid.UUID
    scored_at: datetime
    fraud_probability: float
    decision: str
    model_version: str
    feature_version: str
    policy_reason: str
    review_threshold: float
    block_threshold: float
    features: dict[str, Any]
    explanation: list[dict[str, Any]]
    explanation_base_value: float
    explanation_unit: Literal["probability_delta"]
    status: str
    assignee: str | None = None


class InvestigationResolutionRequest(BaseModel):
    """Human disposition submitted from the investigator queue."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["APPROVE", "REJECT", "ESCALATE"]
    assignee: str = Field(default="local-investigator", min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=2_000)


class InvestigationResolutionResponse(BaseModel):
    """Persisted investigation state after a human action."""

    prediction_id: uuid.UUID
    transaction_id: uuid.UUID
    status: Literal["RESOLVED", "ESCALATED"]
    disposition: Literal["LEGITIMATE", "FRAUD_CONFIRMED", "ESCALATED"]
    assignee: str
    notes: str | None
    actual_is_fraud: int | None
    updated_at: datetime


class SyntheticGenerationRequest(BaseModel):
    """Guard-railed local synthetic-data generation request."""

    model_config = ConfigDict(extra="forbid")

    rows: int = Field(default=5_000, ge=100, le=100_000)
    customers: int = Field(default=1_000, ge=50, le=50_000)
    merchants: int = Field(default=250, ge=20, le=10_000)
    start: datetime = Field(
        default_factory=lambda: datetime.now(UTC) - timedelta(days=120)
    )
    days: int = Field(default=120, ge=7, le=730)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)

    @field_validator("start")
    @classmethod
    def start_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start must include a timezone offset")
        return value.astimezone(UTC)


class SyntheticGenerationResponse(BaseModel):
    """Summary of a synthetic batch written to PostgreSQL."""

    requested_rows: int
    inserted_rows: int
    fraud_rows: int
    fraud_rate: float
    event_start: datetime
    event_end: datetime
    seed: int


class DataSummary(BaseModel):
    """Operational counts read from PostgreSQL."""

    raw_transactions: int
    labeled_transactions: int
    observed_fraud_rate: float | None
    production_predictions: int
    open_reviews: int
    latest_event_timestamp: datetime | None


class PlatformService(BaseModel):
    """One open-source platform component exposed to the UI."""

    name: str
    url: str
    purpose: str


class ModelQuality(BaseModel):
    """Untouched temporal test metrics for the currently loaded model."""

    gate_status: Literal["passed"]
    model_version: str
    feature_version: str
    test_window_start: datetime
    test_window_end: datetime
    test_sample_size: int
    test_fraud_count: int
    data_origin: Literal["synthetic", "external", "production"]
    measured_at: datetime
    metric_definitions: dict[str, str]
    accuracy: float
    balanced_accuracy: float
    pr_auc_average_precision: float
    recall_at_fixed_fpr: float
    precision_at_k: float
    brier_score: float
    expected_calibration_error: float
    simulated_net_monetary_loss_avoided: float


class PlatformStatus(BaseModel):
    """Model readiness and discoverable component entry points."""

    status: Literal["ready", "degraded"]
    model_loaded: bool
    model_name: str | None
    model_version: str | None
    feature_version: str | None
    artifact_checksum: str | None
    previous_model_version: str | None
    model_quality: ModelQuality | None
    synthetic_generation_enabled: bool
    services: list[PlatformService]


class ModelReloadResponse(BaseModel):
    """Model identity loaded from the shared Airflow artifact volume."""

    model_name: str
    model_version: str
    feature_version: str
    artifact_checksum: str
    previous_model_version: str | None


class ModelRollbackResponse(ModelReloadResponse):
    """Identity restored by an atomic rollback."""


class ErrorResponse(BaseModel):
    """Stable API error envelope."""

    error_code: str
    message: str
    correlation_id: str
    details: dict[str, Any] | list[Any] | None = None
