"""Build point-in-time-correct rolling velocity features."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from fraud_platform.db import database_connection
from fraud_platform.features.definitions import (
    FEATURE_VERSION,
    MODEL_FEATURES,
)
from fraud_platform.features.velocity import add_velocity_features


def read_transactions_from_postgres() -> pd.DataFrame:
    """Load labeled and unlabeled history chronologically from PostgreSQL."""

    query = """
        SELECT * FROM raw_transactions
        ORDER BY event_timestamp, transaction_id
    """
    with database_connection() as connection:
        rows = connection.execute(query).fetchall()
    frame = pd.DataFrame.from_records(rows)
    if not frame.empty:
        # Psycopg returns PostgreSQL UUID values as ``uuid.UUID`` objects.
        # Normalize them before writing Parquet because PyArrow has no native
        # inference rule for Python UUID instances.
        frame["transaction_id"] = frame["transaction_id"].astype(str)
    return frame


def write_features_to_postgres(frame: pd.DataFrame) -> int:
    """Persist model features as versioned JSON documents."""

    query = """
        INSERT INTO transaction_features (
            transaction_id, event_timestamp, features, is_fraud, feature_version
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (transaction_id, feature_version) DO UPDATE SET
            features = EXCLUDED.features,
            is_fraud = EXCLUDED.is_fraud,
            feature_version = EXCLUDED.feature_version,
            created_at = NOW()
    """
    records: list[tuple[Any, ...]] = []
    for row in frame.itertuples(index=False):
        record = row._asdict()
        features = {
            name: (
                record[name].item()
                if isinstance(record[name], np.generic)
                else record[name]
            )
            for name in MODEL_FEATURES
        }
        records.append(
            (
                uuid.UUID(str(record["transaction_id"])),
                record["event_timestamp"],
                Jsonb(features),
                (
                    None
                    if pd.isna(record.get("is_fraud"))
                    else int(record["is_fraud"])
                ),
                FEATURE_VERSION,
            )
        )
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(query, records)
    return len(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/transactions.parquet"))
    parser.add_argument("--output", type=Path, default=Path("data/features.parquet"))
    parser.add_argument("--read-postgres", action="store_true")
    parser.add_argument("--write-postgres", action="store_true")
    return parser.parse_args()


def main() -> None:
    """CLI entry point for offline feature generation."""

    args = parse_args()
    transactions = (
        read_transactions_from_postgres()
        if args.read_postgres
        else pd.read_parquet(args.input)
    )
    featured = add_velocity_features(transactions)
    training_targets = featured.loc[featured["is_fraud"].notna()].copy()
    if training_targets.empty:
        raise RuntimeError("No matured labels are available for model training.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    training_targets.to_parquet(args.output, index=False)
    if args.write_postgres:
        write_features_to_postgres(featured)
    print(
        f"Built {len(featured):,} historical feature rows; wrote "
        f"{len(training_targets):,} matured training targets and "
        f"{len(MODEL_FEATURES)} model features."
    )


if __name__ == "__main__":
    main()
