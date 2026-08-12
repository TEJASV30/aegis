from __future__ import annotations

import os
import uuid
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from fraud_platform.config import get_settings
from fraud_platform.db import database_connection
from fraud_platform.features.definitions import FEATURE_VERSION, MODEL_FEATURES
from fraud_platform.models.artifacts import AggregatedExplanation
from fraud_platform.models.decision import DecisionThresholds
from fraud_platform.models.release import ReleasePointer
from fraud_platform.monitoring.performance import monitor_performance
from fraud_platform.serving.main import app
from fraud_platform.serving.model_manager import LoadedRelease


class FixedReviewModel:
    model_name = "decision_core"
    model_version = "e2e-release"
    feature_version = FEATURE_VERSION
    thresholds = DecisionThresholds(0.30, 0.80, 0.01, 1_000_000)

    def predict_proba(self, frame: Any) -> np.ndarray:
        return np.tile(np.array([[0.55, 0.45]]), (len(frame), 1))

    def predict(self, frame: Any) -> np.ndarray:
        return np.array(["Manually Review"] * len(frame))


class ReconstructingExplainer:
    def __call__(self, frame: Any) -> AggregatedExplanation:
        values = np.zeros((len(frame), len(MODEL_FEATURES)))
        values[:, MODEL_FEATURES.index("amount_usd")] = 0.35
        return AggregatedExplanation(values=values, base_values=np.full(len(frame), 0.10))


class FixedManager:
    def __init__(self, release: LoadedRelease) -> None:
        self.release = release

    def snapshot(self) -> LoadedRelease:
        return self.release


def test_decision_review_resolution_and_matured_monitoring(
    require_postgres: None,
) -> None:
    previous_demo = os.environ.get("ENABLE_DEMO_ENDPOINTS")
    os.environ["ENABLE_DEMO_ENDPOINTS"] = "true"
    get_settings.cache_clear()
    transaction_id = str(uuid.uuid4())
    release = LoadedRelease(
        model=FixedReviewModel(),  # type: ignore[arg-type]
        explainer=ReconstructingExplainer(),
        manifest={},
        pointer=ReleasePointer("e2e-release", "test-checksum", "test"),
    )
    payload = {
        "transaction_id": transaction_id,
        "event_timestamp": "2025-05-01T12:00:00+00:00",
        "customer_id": f"e2e-customer-{transaction_id}",
        "merchant_id": "e2e-merchant",
        "device_id": f"e2e-device-{transaction_id}",
        "amount": 250.0,
        "currency": "USD",
        "merchant_category": "electronics",
        "channel": "ecommerce",
        "customer_age": 35,
        "account_age_days": 100,
        "distance_from_home_km": 25.0,
        "is_foreign": 1,
        "device_age_days": 5,
        "failed_attempts_24h": 2,
    }
    try:
        with TestClient(app) as client:
            app.state.model_manager = FixedManager(release)
            first = client.post("/v1/decision", json=payload)
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["decision"] == "Manually Review"
            assert body["queue_admitted"] is True
            assert body["explanation_unit"] == "probability_delta"

            replay = client.post("/v1/decision", json=payload)
            assert replay.status_code == 200
            assert replay.json()["prediction_id"] == body["prediction_id"]
            assert replay.json()["idempotent_replay"] is True

            queue = client.get("/v1/review-queue").json()
            assert any(item["prediction_id"] == body["prediction_id"] for item in queue)
            resolution = client.patch(
                f"/v1/investigations/{body['prediction_id']}",
                json={"action": "REJECT", "assignee": "test-investigator"},
            )
            assert resolution.status_code == 200
            assert resolution.json()["actual_is_fraud"] == 1

        monitoring = monitor_performance(maturity_hours=0)
        assert any(
            snapshot["model_version"] == "e2e-release"
            for snapshot in monitoring["snapshots"]
        )
    finally:
        with database_connection() as connection:
            connection.execute(
                """
                DELETE FROM investigations
                WHERE prediction_id IN (
                    SELECT prediction_id FROM production_predictions
                    WHERE transaction_id = %s
                )
                """,
                (transaction_id,),
            )
            connection.execute(
                "DELETE FROM production_predictions WHERE transaction_id = %s",
                (transaction_id,),
            )
            connection.execute(
                "DELETE FROM raw_transactions WHERE transaction_id = %s",
                (transaction_id,),
            )
            connection.execute(
                "DELETE FROM model_performance_snapshots WHERE model_version = 'e2e-release'"
            )
        if previous_demo is None:
            os.environ.pop("ENABLE_DEMO_ENDPOINTS", None)
        else:
            os.environ["ENABLE_DEMO_ENDPOINTS"] = previous_demo
        get_settings.cache_clear()
