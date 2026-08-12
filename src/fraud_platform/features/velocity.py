"""Pure point-in-time velocity calculations without infrastructure dependencies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fraud_platform.features.currency import amount_to_usd, normalize_currency
from fraud_platform.features.definitions import WINDOWS_SECONDS
from fraud_platform.features.time import utc_series


def _window_arrays(
    timestamps_ns: np.ndarray,
    amounts: np.ndarray,
    window_seconds: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return count, sum, and average over [timestamp-window, timestamp)."""

    lower_bounds = timestamps_ns - window_seconds * 1_000_000_000
    left = np.searchsorted(timestamps_ns, lower_bounds, side="left")
    right = np.searchsorted(timestamps_ns, timestamps_ns, side="left")
    prefix_sum = np.concatenate(([0.0], np.cumsum(amounts, dtype=float)))
    counts = right - left
    sums = prefix_sum[right] - prefix_sum[left]
    averages = np.divide(
        sums,
        counts,
        out=np.zeros_like(sums, dtype=float),
        where=counts > 0,
    )
    return counts.astype(float), sums, averages


def add_velocity_features(transactions: pd.DataFrame) -> pd.DataFrame:
    """Compute customer and device velocities without including the current row.

    All rows sharing the same event timestamp are excluded from one another. This
    conservative tie behavior avoids inventing an ordering that the source system
    did not provide.
    """

    required = {
        "transaction_id",
        "event_timestamp",
        "customer_id",
        "device_id",
        "amount",
        "currency",
    }
    missing = required.difference(transactions.columns)
    if missing:
        raise ValueError(f"Missing required transaction columns: {sorted(missing)}")

    frame = transactions.copy()
    frame["event_timestamp"] = utc_series(frame["event_timestamp"]).array
    frame["currency"] = frame["currency"].map(normalize_currency)
    frame["amount_usd"] = [
        amount_to_usd(amount, currency)
        for amount, currency in zip(
            frame["amount"], frame["currency"], strict=True
        )
    ]
    frame = frame.sort_values("event_timestamp", kind="stable").reset_index(drop=True)
    frame["event_hour"] = frame["event_timestamp"].dt.hour.astype(float)
    frame["event_day_of_week"] = frame["event_timestamp"].dt.dayofweek.astype(float)

    for entity in ("customer", "device"):
        entity_column = f"{entity}_id"
        for window_label, window_seconds in WINDOWS_SECONDS.items():
            count_values = np.zeros(len(frame), dtype=float)
            sum_values = np.zeros(len(frame), dtype=float)
            average_values = np.zeros(len(frame), dtype=float)
            grouped_indices = frame.groupby(entity_column, sort=False).indices
            for indices in grouped_indices.values():
                ordered = np.asarray(indices, dtype=int)
                times = (
                    pd.DatetimeIndex(frame.loc[ordered, "event_timestamp"])
                    .as_unit("ns")
                    .asi8
                )
                amounts = frame.loc[ordered, "amount_usd"].to_numpy(dtype=float)
                counts, sums, averages = _window_arrays(times, amounts, window_seconds)
                count_values[ordered] = counts
                sum_values[ordered] = sums
                average_values[ordered] = averages

            frame[f"{entity}_txn_count_{window_label}"] = count_values
            if entity == "customer":
                frame[f"customer_amount_sum_{window_label}"] = sum_values
                frame[f"customer_amount_avg_{window_label}"] = average_values

    return frame
