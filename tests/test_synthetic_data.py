from __future__ import annotations

from fraud_platform.data.generate_synthetic import generate_transactions


def test_synthetic_fraud_has_sufficient_support_and_observable_patterns() -> None:
    frame = generate_transactions(
        n_transactions=20_000,
        n_customers=2_000,
        n_merchants=500,
        days=120,
        seed=7,
    )

    fraud = frame[frame["is_fraud"] == 1]
    legitimate = frame[frame["is_fraud"] == 0]

    assert 0.035 <= frame["is_fraud"].mean() <= 0.060
    assert len(fraud) >= 700
    assert fraud["amount_usd"].median() > legitimate["amount_usd"].median() * 2.0
    assert fraud["device_age_days"].median() < legitimate["device_age_days"].median()
    assert fraud["failed_attempts_24h"].mean() > legitimate[
        "failed_attempts_24h"
    ].mean()
    assert fraud["is_foreign"].mean() > legitimate["is_foreign"].mean()


def test_synthetic_generation_is_reproducible_and_temporally_ordered() -> None:
    first = generate_transactions(
        n_transactions=500,
        n_customers=100,
        n_merchants=30,
        days=30,
        seed=99,
    )
    second = generate_transactions(
        n_transactions=500,
        n_customers=100,
        n_merchants=30,
        days=30,
        seed=99,
    )

    assert first.equals(second)
    assert first["event_timestamp"].is_monotonic_increasing
