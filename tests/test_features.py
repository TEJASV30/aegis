from __future__ import annotations

import pandas as pd

from fraud_platform.features.velocity import add_velocity_features


def test_velocity_features_exclude_current_and_future_rows() -> None:
    transactions = pd.DataFrame(
        {
            "transaction_id": ["a", "b", "c"],
            "event_timestamp": pd.to_datetime(
                [
                    "2025-01-01T00:00:00Z",
                    "2025-01-01T00:30:00Z",
                    "2025-01-01T02:00:00Z",
                ]
            ),
            "customer_id": ["customer", "customer", "customer"],
            "device_id": ["device", "device", "device"],
            "amount": [10.0, 20.0, 30.0],
            "currency": ["USD", "USD", "USD"],
        }
    )

    featured = add_velocity_features(transactions)

    assert featured["customer_txn_count_1h"].tolist() == [0.0, 1.0, 0.0]
    assert featured["customer_amount_sum_1d"].tolist() == [0.0, 10.0, 30.0]
    assert featured["device_txn_count_7d"].tolist() == [0.0, 1.0, 2.0]


def test_equal_timestamp_rows_do_not_leak_into_one_another() -> None:
    transactions = pd.DataFrame(
        {
            "transaction_id": ["a", "b"],
            "event_timestamp": pd.to_datetime(
                ["2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"]
            ),
            "customer_id": ["customer", "customer"],
            "device_id": ["device", "device"],
            "amount": [10.0, 20.0],
            "currency": ["USD", "USD"],
        }
    )

    featured = add_velocity_features(transactions)

    assert featured["customer_txn_count_7d"].tolist() == [0.0, 0.0]


def test_currency_normalization_applies_before_monetary_velocity() -> None:
    transactions = pd.DataFrame(
        {
            "transaction_id": ["usd", "eur"],
            "event_timestamp": pd.to_datetime(
                ["2025-01-01T00:00:00Z", "2025-01-01T00:30:00Z"]
            ),
            "customer_id": ["customer", "customer"],
            "device_id": ["device", "device"],
            "amount": [100.0, 100.0],
            "currency": ["USD", "EUR"],
        }
    )

    featured = add_velocity_features(transactions)

    assert featured["amount_usd"].tolist() == [100.0, 108.0]
    assert featured["customer_amount_sum_1h"].tolist() == [0.0, 100.0]


def test_naive_timestamps_are_rejected() -> None:
    transactions = pd.DataFrame(
        {
            "transaction_id": ["naive"],
            "event_timestamp": ["2025-01-01T00:00:00"],
            "customer_id": ["customer"],
            "device_id": ["device"],
            "amount": [10.0],
            "currency": ["USD"],
        }
    )

    try:
        add_velocity_features(transactions)
    except ValueError as error:
        assert "timezone offset" in str(error)
    else:
        raise AssertionError("Naive timestamps must not be interpreted implicitly.")


def test_unlabeled_history_contributes_to_labeled_target_velocity() -> None:
    """Label maturity must not change the point-in-time feature history."""

    transactions = pd.DataFrame(
        {
            "transaction_id": ["unlabeled-history", "matured-target"],
            "event_timestamp": pd.to_datetime(
                ["2025-01-01T00:00:00Z", "2025-01-01T00:30:00Z"]
            ),
            "customer_id": ["customer", "customer"],
            "device_id": ["device", "device"],
            "amount": [10_000.0, 20.0],
            "currency": ["JPY", "USD"],
            "is_fraud": [None, 0],
        }
    )

    featured = add_velocity_features(transactions)
    target = featured.loc[featured["transaction_id"] == "matured-target"].iloc[0]

    assert target["customer_txn_count_1h"] == 1.0
    assert target["customer_amount_sum_1h"] == 67.0
