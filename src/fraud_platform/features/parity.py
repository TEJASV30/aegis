"""Offline/PostgreSQL point-in-time feature parity gate."""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd

from fraud_platform.db import database_connection
from fraud_platform.features.definitions import CATEGORICAL_FEATURES, MODEL_FEATURES
from fraud_platform.features.online import PostgreSQLFeatureStore
from fraud_platform.features.velocity import add_velocity_features


def _history(limit: int) -> pd.DataFrame:
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM raw_transactions
            ORDER BY event_timestamp, transaction_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return pd.DataFrame.from_records(rows)


def validate_feature_parity(
    history_limit: int = 5_000,
    sample_size: int = 25,
    tolerance: float = 1e-8,
) -> dict[str, Any]:
    """Compare shared offline features with independent PostgreSQL queries."""

    history = _history(history_limit)
    if len(history) < sample_size:
        raise RuntimeError("Not enough history for parity validation.")
    offline = add_velocity_features(history)
    candidates = offline.tail(sample_size)
    store = PostgreSQLFeatureStore()
    failures: list[dict[str, Any]] = []
    for row in candidates.to_dict(orient="records"):
        online = store.calculate(row)
        for feature in MODEL_FEATURES:
            expected = row[feature]
            actual = online[feature]
            if feature in CATEGORICAL_FEATURES:
                equal = str(expected) == str(actual)
            else:
                equal = bool(
                    np.isclose(float(expected), float(actual), atol=tolerance, rtol=0.0)
                )
            if not equal:
                failures.append(
                    {
                        "transaction_id": str(row["transaction_id"]),
                        "feature": feature,
                        "offline": expected,
                        "online": actual,
                    }
                )
    if failures:
        preview = json.dumps(failures[:10], default=str)
        raise RuntimeError(f"Feature parity failed ({len(failures)} mismatches): {preview}")
    return {
        "status": "passed",
        "history_rows": len(history),
        "transactions_compared": len(candidates),
        "features_per_transaction": len(MODEL_FEATURES),
        "absolute_tolerance": tolerance,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-limit", type=int, default=5_000)
    parser.add_argument("--sample-size", type=int, default=25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            validate_feature_parity(args.history_limit, args.sample_size), indent=2
        )
    )


if __name__ == "__main__":
    main()
