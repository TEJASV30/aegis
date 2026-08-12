"""Validate that PostgreSQL history is fit for temporal feature generation."""

from __future__ import annotations

import json

from fraud_platform.db import database_connection
from fraud_platform.features.currency import USD_PER_UNIT


def validate_source(minimum_rows: int = 1_000) -> dict[str, object]:
    """Validate history while allowing recent outcomes to remain unlabeled.

    Every event participates in point-in-time velocity history. Only rows with a
    matured label are eligible training targets, so recent production events do
    not have to be discarded or assigned a guessed outcome.
    """

    with database_connection() as connection:
        summary = connection.execute(
            """
            WITH rates(currency, usd_per_unit) AS (
                VALUES ('USD', 1.0), ('EUR', 1.08), ('GBP', 1.27),
                       ('INR', 0.012), ('JPY', 0.0067), ('SGD', 0.74)
            )
            SELECT COUNT(*) AS rows,
                   COUNT(*) FILTER (WHERE transaction.is_fraud IS NOT NULL)
                       AS labeled_rows,
                   COUNT(*) FILTER (WHERE transaction.is_fraud IS NULL)
                       AS unlabeled_rows,
                   COUNT(DISTINCT event_timestamp) AS distinct_timestamps,
                   COUNT(*) FILTER (WHERE rates.currency IS NULL)
                       AS unsupported_currency_rows,
                   COUNT(*) FILTER (
                       WHERE transaction.amount_usd IS NULL
                          OR transaction.amount_usd < 0
                   )
                       AS invalid_amount_rows,
                   COUNT(*) FILTER (
                       WHERE rates.currency IS NOT NULL
                         AND ABS(
                             transaction.amount_usd
                             - transaction.amount * rates.usd_per_unit
                         ) > GREATEST(
                             0.000001,
                             ABS(transaction.amount * rates.usd_per_unit) * 1e-9
                         )
                   ) AS currency_skew_rows,
                   MIN(transaction.event_timestamp) AS event_start,
                   MAX(transaction.event_timestamp) AS event_end
            FROM raw_transactions AS transaction
            LEFT JOIN rates ON rates.currency = UPPER(transaction.currency)
            """
        ).fetchone()
    if summary is None:
        raise RuntimeError("PostgreSQL returned no source summary.")
    failures: list[str] = []
    if int(summary["labeled_rows"]) < minimum_rows:
        failures.append(f"requires at least {minimum_rows} matured labels")
    if int(summary["distinct_timestamps"]) < 20:
        failures.append("requires at least 20 distinct event timestamps")
    if int(summary["unsupported_currency_rows"]):
        failures.append("contains unsupported currencies")
    if int(summary["invalid_amount_rows"]):
        failures.append("contains invalid canonical amounts")
    if int(summary["currency_skew_rows"]):
        failures.append("contains canonical currency conversion skew")
    if failures:
        raise RuntimeError("Source validation failed: " + "; ".join(failures))
    return {
        **dict(summary),
        "history_context_rows": int(summary["rows"]),
        "training_target_rows": int(summary["labeled_rows"]),
        "unlabeled_policy": "included_in_history_excluded_from_training_targets",
        "supported_currencies": sorted(USD_PER_UNIT),
        "status": "passed",
    }


def main() -> None:
    print(json.dumps(validate_source(), indent=2, default=str))


if __name__ == "__main__":
    main()
