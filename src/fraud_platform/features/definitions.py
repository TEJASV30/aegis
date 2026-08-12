"""Canonical feature contract shared by training, serving, and monitoring."""

from __future__ import annotations

import hashlib
import json

from fraud_platform.features.currency import CURRENCY_RATE_VERSION

FEATURE_VERSION = "v2"
WINDOWS_SECONDS: dict[str, int] = {"1h": 3_600, "1d": 86_400, "7d": 604_800}

BASE_NUMERIC_FEATURES = [
    "amount_usd",
    "customer_age",
    "account_age_days",
    "distance_from_home_km",
    "is_foreign",
    "device_age_days",
    "failed_attempts_24h",
    "event_hour",
    "event_day_of_week",
]

CATEGORICAL_FEATURES = [
    "currency",
    "merchant_category",
    "channel",
]

VELOCITY_FEATURES = [
    f"{entity}_{metric}_{window}"
    for window in WINDOWS_SECONDS
    for entity, metrics in {
        "customer": ("txn_count", "amount_sum", "amount_avg"),
        "device": ("txn_count",),
    }.items()
    for metric in metrics
]

NUMERIC_FEATURES = BASE_NUMERIC_FEATURES + VELOCITY_FEATURES
MODEL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def feature_schema_payload() -> dict[str, object]:
    """Return the complete versioned feature contract used for compatibility."""

    return {
        "feature_version": FEATURE_VERSION,
        "currency_rate_version": CURRENCY_RATE_VERSION,
        "windows_seconds": WINDOWS_SECONDS,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "model_features": MODEL_FEATURES,
    }


def feature_schema_fingerprint() -> str:
    """Return a deterministic SHA-256 fingerprint for the feature contract."""

    canonical = json.dumps(
        feature_schema_payload(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
