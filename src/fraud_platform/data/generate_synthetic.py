"""Generate reproducible, temporally ordered synthetic card transactions."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fraud_platform.features.currency import USD_PER_UNIT

NAMESPACE = uuid.UUID("93b9dddc-6fe8-4bf2-83fc-fca4fd899a31")


def generate_transactions(
    n_transactions: int = 100_000,
    n_customers: int = 8_000,
    n_merchants: int = 1_500,
    start: str = "2025-01-01T00:00:00Z",
    days: int = 120,
    seed: int = 42,
) -> pd.DataFrame:
    """Create sorted transactions with behavioral and entity-level signals.

    Fraud labels are sampled from a non-linear risk process. Entity identifiers are
    intentionally retained for velocity aggregation but excluded from the model so
    the risk engine does not merely memorize synthetic identities.
    """

    if min(n_transactions, n_customers, n_merchants, days) <= 0:
        raise ValueError("All size parameters must be positive.")

    rng = np.random.default_rng(seed)
    start_ts = pd.Timestamp(start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")

    timestamps = start_ts + pd.to_timedelta(
        np.sort(rng.uniform(0.0, days * 86_400.0, n_transactions)), unit="s"
    )

    customer_ids = np.array([f"cust_{index:07d}" for index in range(n_customers)])
    merchant_ids = np.array([f"merch_{index:06d}" for index in range(n_merchants)])
    customer_weights = rng.pareto(1.8, n_customers) + 0.15
    customer_weights /= customer_weights.sum()
    customer_index = rng.choice(n_customers, n_transactions, p=customer_weights)
    merchant_index = rng.integers(0, n_merchants, n_transactions)

    customer_age = rng.integers(18, 86, n_customers)
    account_age_days = rng.integers(5, 5_000, n_customers)
    typical_amount = rng.lognormal(mean=3.3, sigma=0.75, size=n_customers)
    typical_distance = rng.gamma(shape=1.8, scale=6.0, size=n_customers)

    categories = np.array(
        [
            "grocery",
            "fuel",
            "restaurant",
            "retail",
            "travel",
            "electronics",
            "digital_goods",
            "cash_withdrawal",
        ]
    )
    category_probabilities = np.array([0.23, 0.12, 0.18, 0.18, 0.07, 0.07, 0.09, 0.06])
    merchant_category_map = rng.choice(
        categories, n_merchants, p=category_probabilities
    )
    merchant_categories = merchant_category_map[merchant_index]

    channels = rng.choice(
        np.array(["pos", "ecommerce", "mobile", "atm"]),
        n_transactions,
        p=[0.49, 0.28, 0.17, 0.06],
    )
    currencies = rng.choice(
        np.array(["USD", "EUR", "GBP", "INR", "SGD"]),
        n_transactions,
        p=[0.62, 0.13, 0.08, 0.12, 0.05],
    )
    is_foreign = (
        rng.random(n_transactions)
        < np.where(currencies == "USD", 0.025, 0.35)
    ).astype(np.int8)

    category_multiplier = pd.Series(merchant_categories).map(
        {
            "grocery": 0.7,
            "fuel": 0.8,
            "restaurant": 0.65,
            "retail": 1.2,
            "travel": 3.0,
            "electronics": 2.5,
            "digital_goods": 0.9,
            "cash_withdrawal": 1.5,
        }
    ).to_numpy(dtype=float)
    amount_usd = (
        typical_amount[customer_index]
        * category_multiplier
        * rng.lognormal(mean=0.0, sigma=0.72, size=n_transactions)
    ).clip(0.5, 25_000.0)

    distance = (
        typical_distance[customer_index]
        + rng.gamma(1.2, 4.0, n_transactions)
        + is_foreign * rng.gamma(2.0, 240.0, n_transactions)
    )
    customer_device_slot = rng.choice(
        np.array([0, 1, 2, 99]),
        n_transactions,
        p=[0.82, 0.12, 0.045, 0.015],
    )
    device_ids = np.where(
        customer_device_slot == 99,
        np.char.add("shared_", rng.integers(0, 600, n_transactions).astype(str)),
        np.char.add(
            np.char.add(customer_ids[customer_index], "_device_"),
            customer_device_slot.astype(str),
        ),
    ).astype(object)
    device_age_days = np.where(
        customer_device_slot == 0,
        rng.integers(30, 1_600, n_transactions),
        np.where(
            customer_device_slot == 99,
            rng.integers(0, 5, n_transactions),
            rng.integers(1, 180, n_transactions),
        ),
    )
    failed_attempts = rng.poisson(
        0.08
        + 0.8 * (customer_device_slot == 99)
        + 0.2 * (channels == "ecommerce")
    ).clip(0, 12)

    # Fraud is generated from observable attack archetypes instead of an almost
    # entirely random rare-event logit. This produces enough positive support for
    # temporal calibration while retaining legitimate high-risk edge cases and a
    # deliberately harder friendly-fraud segment.
    history_span = timestamps.max() - timestamps.min()
    time_progress = (
        np.zeros(n_transactions, dtype=float)
        if history_span == pd.Timedelta(0)
        else np.asarray(
            (timestamps - timestamps.min()) / history_span,
            dtype=float,
        )
    )
    fraud_probability = (
        0.030
        + 0.012 * time_progress
        + 0.008 * (channels == "ecommerce")
        + 0.005
        * np.isin(merchant_categories, ["digital_goods", "electronics"])
    )
    is_fraud = (rng.random(n_transactions) < fraud_probability).astype(np.int8)
    fraud_rows = is_fraud == 1

    fraud_type = np.full(n_transactions, None, dtype=object)
    fraud_type[fraud_rows] = rng.choice(
        np.array(["account_takeover", "stolen_card", "friendly_fraud"]),
        fraud_rows.sum(),
        p=[0.48, 0.37, 0.15],
    )

    account_takeover = fraud_type == "account_takeover"
    stolen_card = fraud_type == "stolen_card"
    friendly_fraud = fraud_type == "friendly_fraud"

    def replace_choice(mask: np.ndarray, values: list[str], p: list[float]) -> None:
        count = int(mask.sum())
        if count:
            merchant_categories[mask] = rng.choice(values, count, p=p)

    # Account takeover: credential attacks from new shared devices, commonly
    # followed by a remote, foreign, high-value purchase.
    ato_count = int(account_takeover.sum())
    if ato_count:
        channels[account_takeover] = rng.choice(
            ["ecommerce", "mobile"], ato_count, p=[0.78, 0.22]
        )
        is_foreign[account_takeover] = (
            rng.random(ato_count) < 0.82
        ).astype(np.int8)
        device_ids[account_takeover] = np.char.add(
            "attack_device_", rng.integers(0, 45, ato_count).astype(str)
        )
        device_age_days[account_takeover] = rng.integers(0, 4, ato_count)
        failed_attempts[account_takeover] = (
            2 + rng.poisson(2.6, ato_count)
        ).clip(2, 12)
        distance[account_takeover] = rng.gamma(2.2, 180.0, ato_count) + 80.0
        amount_usd[account_takeover] *= rng.lognormal(2.25, 0.45, ato_count)
        replace_choice(
            account_takeover,
            ["digital_goods", "electronics", "travel"],
            [0.42, 0.38, 0.20],
        )

    # Stolen cards include both card-not-present and point-of-sale abuse. The
    # smaller pool of mule devices creates learnable one-hour/day velocity bursts.
    stolen_count = int(stolen_card.sum())
    if stolen_count:
        channels[stolen_card] = rng.choice(
            ["ecommerce", "pos", "mobile"],
            stolen_count,
            p=[0.58, 0.30, 0.12],
        )
        is_foreign[stolen_card] = (
            rng.random(stolen_count) < 0.58
        ).astype(np.int8)
        device_ids[stolen_card] = np.char.add(
            "mule_device_", rng.integers(0, 70, stolen_count).astype(str)
        )
        device_age_days[stolen_card] = rng.integers(0, 9, stolen_count)
        failed_attempts[stolen_card] = rng.poisson(1.4, stolen_count).clip(0, 8)
        distance[stolen_card] = rng.gamma(2.0, 115.0, stolen_count) + 35.0
        amount_usd[stolen_card] *= rng.lognormal(1.75, 0.55, stolen_count)
        replace_choice(
            stolen_card,
            ["electronics", "retail", "travel", "cash_withdrawal"],
            [0.38, 0.26, 0.20, 0.16],
        )

    # Friendly fraud is intentionally less separable: the known customer and
    # device make it a useful hard-positive segment rather than a perfect toy set.
    friendly_count = int(friendly_fraud.sum())
    if friendly_count:
        channels[friendly_fraud] = rng.choice(
            ["ecommerce", "mobile", "pos"],
            friendly_count,
            p=[0.52, 0.18, 0.30],
        )
        is_foreign[friendly_fraud] = (
            rng.random(friendly_count) < 0.12
        ).astype(np.int8)
        failed_attempts[friendly_fraud] = rng.binomial(1, 0.18, friendly_count)
        amount_usd[friendly_fraud] *= rng.lognormal(1.20, 0.50, friendly_count)
        replace_choice(
            friendly_fraud,
            ["digital_goods", "retail", "travel", "restaurant"],
            [0.38, 0.30, 0.17, 0.15],
        )

    # Legitimate travellers and new-device customers stop the task from becoming
    # a single-rule lookup and give calibration genuine overlap to learn.
    legitimate_edge = (~fraud_rows) & (rng.random(n_transactions) < 0.018)
    edge_count = int(legitimate_edge.sum())
    if edge_count:
        is_foreign[legitimate_edge] = (
            rng.random(edge_count) < 0.72
        ).astype(np.int8)
        distance[legitimate_edge] += rng.gamma(2.0, 135.0, edge_count)
        device_age_days[legitimate_edge] = rng.integers(2, 45, edge_count)
        failed_attempts[legitimate_edge] = rng.binomial(1, 0.22, edge_count)
        replace_choice(
            legitimate_edge,
            ["travel", "electronics", "retail"],
            [0.55, 0.20, 0.25],
        )

    amount_usd = amount_usd.clip(0.5, 25_000.0)
    usd_rates = np.array([USD_PER_UNIT[str(currency)] for currency in currencies])
    amount = np.round(amount_usd / usd_rates, 2)
    amount_usd = amount * usd_rates
    distance = distance.clip(0.0, 5_000.0)

    transaction_ids = [
        str(uuid.uuid5(NAMESPACE, f"{seed}:{index}:{timestamps[index].value}"))
        for index in range(n_transactions)
    ]
    frame = pd.DataFrame(
        {
            "transaction_id": transaction_ids,
            "event_timestamp": timestamps,
            "customer_id": customer_ids[customer_index],
            "merchant_id": merchant_ids[merchant_index],
            "device_id": device_ids,
            "amount": amount.round(2),
            "amount_usd": amount_usd.round(6),
            "currency": currencies,
            "merchant_category": merchant_categories,
            "channel": channels,
            "customer_age": customer_age[customer_index],
            "account_age_days": account_age_days[customer_index],
            "distance_from_home_km": distance.round(3),
            "is_foreign": is_foreign,
            "device_age_days": device_age_days,
            "failed_attempts_24h": failed_attempts,
            "is_fraud": is_fraud,
            "fraud_type": fraud_type,
        }
    )
    return frame.sort_values("event_timestamp", kind="stable").reset_index(drop=True)


def write_transactions_to_postgres(frame: pd.DataFrame) -> int:
    """Insert synthetic transactions idempotently and return rows inserted."""

    from fraud_platform.db import database_connection

    columns = list(frame.columns)
    query = f"""
        INSERT INTO raw_transactions ({', '.join(columns)})
        VALUES ({', '.join(['%s'] * len(columns))})
        ON CONFLICT (transaction_id) DO NOTHING
    """
    records: list[tuple[Any, ...]] = []
    for row in frame.itertuples(index=False, name=None):
        converted = [
            value.item() if isinstance(value, np.generic) else value for value in row
        ]
        converted[0] = uuid.UUID(str(converted[0]))
        records.append(tuple(converted))
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(query, records)
            inserted = cursor.rowcount
    return max(int(inserted), 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--customers", type=int, default=8_000)
    parser.add_argument("--merchants", type=int, default=1_500)
    parser.add_argument("--start", default="2025-01-01T00:00:00Z")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output", type=Path, default=Path("data/transactions.parquet")
    )
    parser.add_argument("--write-postgres", action="store_true")
    return parser.parse_args()


def main() -> None:
    """CLI entry point for reproducible data generation."""

    args = parse_args()
    frame = generate_transactions(
        n_transactions=args.rows,
        n_customers=args.customers,
        n_merchants=args.merchants,
        start=args.start,
        days=args.days,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output, index=False)
    if args.write_postgres:
        write_transactions_to_postgres(frame)
    fraud_rate = float(frame["is_fraud"].mean())
    print(f"Wrote {len(frame):,} ordered transactions to {args.output}.")
    print(f"Simulated fraud rate: {fraud_rate:.4%}.")


if __name__ == "__main__":
    main()
