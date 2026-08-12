"""Reproducible Aegis decision-serving load test."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

from locust import HttpUser, between, events, task


class DecisionUser(HttpUser):
    """Submit valid, unique transactions and expose server timing components."""

    wait_time = between(0.05, 0.25)

    @task
    def score_transaction(self) -> None:
        transaction_id = str(uuid.uuid4())
        payload = {
            "transaction_id": transaction_id,
            "event_timestamp": datetime.now(UTC).isoformat(),
            "customer_id": f"load_customer_{random.randint(1, 200):04d}",
            "merchant_id": f"load_merchant_{random.randint(1, 50):03d}",
            "device_id": f"load_device_{random.randint(1, 300):04d}",
            "amount": round(random.uniform(5, 2_500), 2),
            "currency": random.choice(["USD", "EUR", "GBP", "INR", "JPY", "SGD"]),
            "merchant_category": random.choice(
                ["grocery", "fuel", "retail", "travel", "electronics", "digital_goods"]
            ),
            "channel": random.choice(["pos", "ecommerce", "mobile", "atm"]),
            "customer_age": random.randint(18, 85),
            "account_age_days": random.randint(1, 4_000),
            "distance_from_home_km": round(random.uniform(0, 900), 2),
            "is_foreign": int(random.random() < 0.15),
            "device_age_days": random.randint(0, 1_500),
            "failed_attempts_24h": random.randint(0, 6),
        }
        with self.client.post(
            "/v1/decision",
            json=payload,
            name="end-to-end decision",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                return
            body = response.json()
            response.success()
            for name, field in (
                ("server feature query", "feature_latency_ms"),
                ("server model + explanation", "model_latency_ms"),
                ("server end to end", "inference_latency_ms"),
            ):
                events.request.fire(
                    request_type="SERVER",
                    name=name,
                    response_time=float(body[field]),
                    response_length=0,
                    exception=None,
                    context={"model_version": body["model_version"]},
                )
