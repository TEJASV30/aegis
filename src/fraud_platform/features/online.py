"""Point-in-time online velocity calculation backed by PostgreSQL."""

from __future__ import annotations

from typing import Any

from fraud_platform.db import database_connection
from fraud_platform.features.currency import amount_to_usd, normalize_currency
from fraud_platform.features.definitions import MODEL_FEATURES, WINDOWS_SECONDS
from fraud_platform.features.time import utc_timestamp


class PostgreSQLFeatureStore:
    """Calculate online features from prior raw transactions only."""

    def _velocity_query(self) -> str:
        expressions: list[str] = []
        intervals = {"1h": "1 hour", "1d": "1 day", "7d": "7 days"}
        for label in WINDOWS_SECONDS:
            interval = intervals[label]
            customer_filter = (
                "customer_id = %(customer_id)s "
                f"AND event_timestamp >= %(event_timestamp)s - INTERVAL '{interval}'"
            )
            device_filter = (
                "device_id = %(device_id)s "
                f"AND event_timestamp >= %(event_timestamp)s - INTERVAL '{interval}'"
            )
            expressions.extend(
                [
                    f"COUNT(*) FILTER (WHERE {customer_filter}) "
                    f"AS customer_txn_count_{label}",
                    f"COALESCE(SUM(amount_usd) FILTER (WHERE {customer_filter}), 0) "
                    f"AS customer_amount_sum_{label}",
                    f"COALESCE(AVG(amount_usd) FILTER (WHERE {customer_filter}), 0) "
                    f"AS customer_amount_avg_{label}",
                    f"COUNT(*) FILTER (WHERE {device_filter}) "
                    f"AS device_txn_count_{label}",
                ]
            )
        return f"""
            SELECT {", ".join(expressions)}
            FROM raw_transactions
            WHERE event_timestamp < %(event_timestamp)s
              AND event_timestamp >= %(event_timestamp)s - INTERVAL '7 days'
              AND (customer_id = %(customer_id)s OR device_id = %(device_id)s)
        """

    def calculate(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """Combine request attributes with prior-only PostgreSQL aggregations."""

        event_timestamp = utc_timestamp(transaction["event_timestamp"]).to_pydatetime()
        currency = normalize_currency(str(transaction["currency"]))
        parameters = {
            "customer_id": transaction["customer_id"],
            "device_id": transaction["device_id"],
            "event_timestamp": event_timestamp,
        }
        with database_connection() as connection:
            velocity = connection.execute(
                self._velocity_query(), parameters
            ).fetchone()
        if velocity is None:
            raise RuntimeError("PostgreSQL did not return velocity aggregates.")
        combined = {
            **transaction,
            **dict(velocity),
            "currency": currency,
            "amount_usd": amount_to_usd(float(transaction["amount"]), currency),
            "event_hour": float(event_timestamp.hour),
            "event_day_of_week": float(event_timestamp.weekday()),
        }
        return {name: combined[name] for name in MODEL_FEATURES}
