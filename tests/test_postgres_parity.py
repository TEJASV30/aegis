from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from fraud_platform.db import database_connection
from fraud_platform.features.definitions import MODEL_FEATURES
from fraud_platform.features.online import PostgreSQLFeatureStore
from fraud_platform.features.velocity import add_velocity_features


def test_offline_online_parity_at_equal_time_and_window_boundary(
    require_postgres: None,
) -> None:
    customer = f"parity-customer-{uuid.uuid4()}"
    device = f"parity-device-{uuid.uuid4()}"
    timestamps = pd.to_datetime(
        [
            "2025-04-01T10:00:00Z",
            "2025-04-01T11:00:00Z",
            "2025-04-01T11:00:00Z",
        ]
    )
    ids = [uuid.uuid4() for _ in range(3)]
    history = pd.DataFrame(
        {
            "transaction_id": ids,
            "event_timestamp": timestamps,
            "customer_id": [customer] * 3,
            "merchant_id": ["merchant"] * 3,
            "device_id": [device] * 3,
            "amount": [100.0, 25.0, 35.0],
            "amount_usd": [108.0, 25.0, 35.0],
            "currency": ["EUR", "USD", "USD"],
            "merchant_category": ["grocery"] * 3,
            "channel": ["pos"] * 3,
            "customer_age": [40] * 3,
            "account_age_days": [500] * 3,
            "distance_from_home_km": [2.0] * 3,
            "is_foreign": [0] * 3,
            "device_age_days": [300] * 3,
            "failed_attempts_24h": [0] * 3,
            "is_fraud": [0] * 3,
            "fraud_type": [None] * 3,
        }
    )
    target = {
        **history.iloc[-1].to_dict(),
        "transaction_id": uuid.uuid4(),
        "amount": 10.0,
        "amount_usd": 10.0,
    }
    with database_connection() as connection:
        for row in history.iloc[:2].to_dict(orient="records"):
            connection.execute(
                """
                INSERT INTO raw_transactions (
                    transaction_id, event_timestamp, customer_id, merchant_id,
                    device_id, amount, amount_usd, currency, merchant_category,
                    channel, customer_age, account_age_days, distance_from_home_km,
                    is_foreign, device_age_days, failed_attempts_24h, is_fraud,
                    fraud_type
                ) VALUES (
                    %(transaction_id)s, %(event_timestamp)s, %(customer_id)s,
                    %(merchant_id)s, %(device_id)s, %(amount)s, %(amount_usd)s,
                    %(currency)s, %(merchant_category)s, %(channel)s,
                    %(customer_age)s, %(account_age_days)s,
                    %(distance_from_home_km)s, %(is_foreign)s,
                    %(device_age_days)s, %(failed_attempts_24h)s, %(is_fraud)s,
                    %(fraud_type)s
                )
                """,
                row,
            )
    try:
        offline = add_velocity_features(
            pd.concat([history.iloc[:2], pd.DataFrame([target])], ignore_index=True)
        ).iloc[-1]
        online = PostgreSQLFeatureStore().calculate(target)
        for feature in MODEL_FEATURES:
            if isinstance(online[feature], str):
                assert str(offline[feature]) == online[feature]
            else:
                assert np.isclose(
                    float(offline[feature]), float(online[feature]), atol=1e-8
                ), feature
        assert online["customer_txn_count_1h"] == 1
        assert online["customer_amount_sum_1h"] == 108.0
    finally:
        with database_connection() as connection:
            connection.execute(
                "DELETE FROM raw_transactions WHERE customer_id = %s", (customer,)
            )
