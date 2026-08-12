"""Strict chronological dataset partitions."""

from __future__ import annotations

import pandas as pd

from fraud_platform.features.time import utc_series


def temporal_split(
    frame: pd.DataFrame,
    train_fraction: float = 0.65,
    validation_fraction: float = 0.20,
) -> dict[str, pd.DataFrame]:
    """Make strict train/calibration/selection/test partitions by event time.

    The validation period is divided chronologically: its older half fits Platt
    calibration and its newer half selects models and decision thresholds. The test
    partition remains untouched until one model and policy have been selected.
    """

    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("Train plus validation fractions must be less than one.")
    ordered = frame.copy()
    ordered["event_timestamp"] = utc_series(ordered["event_timestamp"]).array
    ordered = ordered.sort_values("event_timestamp", kind="stable").reset_index(
        drop=True
    )
    unique_times = ordered["event_timestamp"].drop_duplicates().to_numpy()
    if len(unique_times) < 20:
        raise ValueError("At least 20 distinct timestamps are required.")

    train_index = max(1, int(len(unique_times) * train_fraction))
    validation_end_index = max(
        train_index + 2,
        int(len(unique_times) * (train_fraction + validation_fraction)),
    )
    validation_mid_index = train_index + (validation_end_index - train_index) // 2
    train_end = unique_times[train_index]
    calibration_end = unique_times[validation_mid_index]
    validation_end = unique_times[validation_end_index]

    partitions = {
        "train": ordered[ordered["event_timestamp"] < train_end],
        "calibration": ordered[
            (ordered["event_timestamp"] >= train_end)
            & (ordered["event_timestamp"] < calibration_end)
        ],
        "selection": ordered[
            (ordered["event_timestamp"] >= calibration_end)
            & (ordered["event_timestamp"] < validation_end)
        ],
        "test": ordered[ordered["event_timestamp"] >= validation_end],
    }
    for name, partition in partitions.items():
        if partition.empty or partition["is_fraud"].nunique() != 2:
            raise ValueError(f"Partition '{name}' must be non-empty with both classes.")
    return {name: value.reset_index(drop=True) for name, value in partitions.items()}
