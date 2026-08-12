"""Reproducible uncertainty and segment diagnostics for fraud releases."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss

from fraud_platform.models.metrics import (
    expected_calibration_error,
    recall_at_fixed_fpr,
)


def bootstrap_intervals(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    *,
    target_fpr: float,
    iterations: int = 200,
    seed: int = 42,
) -> dict[str, dict[str, float]]:
    """Return percentile 95% intervals from deterministic stratified bootstraps."""

    labels = np.asarray(y_true, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    positive = np.flatnonzero(labels == 1)
    negative = np.flatnonzero(labels == 0)
    if not len(positive) or not len(negative):
        raise ValueError("Bootstrap intervals require both classes.")
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {
        "pr_auc_average_precision": [],
        "recall_at_fixed_fpr": [],
        "brier_score": [],
        "expected_calibration_error": [],
    }
    for _ in range(iterations):
        indices = np.concatenate(
            (
                rng.choice(positive, len(positive), replace=True),
                rng.choice(negative, len(negative), replace=True),
            )
        )
        rng.shuffle(indices)
        sampled_labels = labels[indices]
        sampled_scores = scores[indices]
        samples["pr_auc_average_precision"].append(
            float(average_precision_score(sampled_labels, sampled_scores))
        )
        samples["recall_at_fixed_fpr"].append(
            recall_at_fixed_fpr(sampled_labels, sampled_scores, target_fpr)
        )
        samples["brier_score"].append(
            float(brier_score_loss(sampled_labels, sampled_scores))
        )
        samples["expected_calibration_error"].append(
            expected_calibration_error(sampled_labels, sampled_scores)
        )
    return {
        name: {
            "lower_95": float(np.quantile(values, 0.025)),
            "median": float(np.quantile(values, 0.5)),
            "upper_95": float(np.quantile(values, 0.975)),
        }
        for name, values in samples.items()
    }


def _segment_row(
    labels: np.ndarray,
    probabilities: np.ndarray,
    target_fpr: float,
) -> dict[str, float | int | None]:
    fraud_count = int(labels.sum())
    legitimate_count = int((labels == 0).sum())
    both_classes = fraud_count > 0 and legitimate_count > 0
    return {
        "sample_count": int(len(labels)),
        "fraud_count": fraud_count,
        "fraud_rate": float(labels.mean()) if len(labels) else None,
        "pr_auc_average_precision": (
            float(average_precision_score(labels, probabilities))
            if both_classes
            else None
        ),
        "recall_at_fixed_fpr": (
            recall_at_fixed_fpr(labels, probabilities, target_fpr)
            if both_classes
            else None
        ),
        "brier_score": (
            float(brier_score_loss(labels, probabilities)) if len(labels) else None
        ),
        "expected_calibration_error": (
            expected_calibration_error(labels, probabilities) if len(labels) else None
        ),
    }


def segment_metrics(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    target_fpr: float,
    columns: Iterable[str] = (
        "channel",
        "currency",
        "merchant_category",
        "is_foreign",
        "fraud_type",
    ),
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    """Evaluate release behavior for operational and sensitive slices."""

    working = frame.reset_index(drop=True).copy()
    working["age_range"] = pd.cut(
        working["customer_age"],
        bins=[17, 25, 40, 60, 120],
        labels=["18-25", "26-40", "41-60", "61+"],
        include_lowest=True,
    ).astype("string")
    working["__probability"] = np.asarray(probabilities, dtype=float)
    results: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for column in [*columns, "age_range"]:
        if column not in working:
            continue
        results[column] = {}
        for value, segment in working.groupby(column, dropna=False, observed=True):
            labels = segment["is_fraud"].to_numpy(dtype=int)
            scores = segment["__probability"].to_numpy(dtype=float)
            results[column][str(value)] = _segment_row(
                labels, scores, target_fpr
            )
    return results


def calibration_comparison(
    y_true: np.ndarray,
    uncalibrated: np.ndarray,
    calibrated: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Compare ranking and probability quality before and after calibration."""

    labels = np.asarray(y_true, dtype=int)

    def metrics(values: np.ndarray) -> dict[str, float]:
        return {
            "pr_auc_average_precision": float(
                average_precision_score(labels, values)
            ),
            "brier_score": float(brier_score_loss(labels, values)),
            "expected_calibration_error": expected_calibration_error(labels, values),
        }

    return {
        "uncalibrated": metrics(np.asarray(uncalibrated, dtype=float)),
        "calibrated": metrics(np.asarray(calibrated, dtype=float)),
    }
