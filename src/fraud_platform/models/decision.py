"""Capacity- and risk-aware three-way decision policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
from sklearn.metrics import precision_recall_curve, roc_curve

Decision = Literal["Approve", "Manually Review", "Block"]


@dataclass(frozen=True)
class DecisionThresholds:
    """Calibrated probability cutoffs for the three-way policy."""

    review: float
    block: float
    target_fpr: float
    review_capacity: int
    review_target_recall: float = 0.90

    def __post_init__(self) -> None:
        if not 0.0 <= self.review <= self.block <= 1.0:
            raise ValueError("Expected 0 <= review threshold <= block threshold <= 1.")
        if not 0.0 < self.review_target_recall <= 1.0:
            raise ValueError("review_target_recall must be in (0, 1].")

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def threshold_at_fixed_fpr(
    y_true: np.ndarray, probabilities: np.ndarray, target_fpr: float
) -> float:
    """Choose the lowest threshold whose empirical FPR is within the budget."""

    fpr, _, thresholds = roc_curve(y_true, probabilities)
    valid = np.flatnonzero(fpr <= target_fpr)
    if valid.size == 0:
        return 1.0
    threshold = float(thresholds[valid[-1]])
    return float(np.clip(threshold, 0.0, 1.0))


def threshold_at_best_f1(
    y_true: np.ndarray, probabilities: np.ndarray
) -> float:
    """Choose the validation threshold with the strongest fraud F1 tradeoff."""

    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    if thresholds.size == 0:
        return 0.5
    numerator = 2.0 * precision[:-1] * recall[:-1]
    denominator = np.maximum(precision[:-1] + recall[:-1], 1e-12)
    best_index = int(np.nanargmax(numerator / denominator))
    return float(np.clip(thresholds[best_index], 0.0, 1.0))


def threshold_at_target_recall(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    target_recall: float,
) -> float:
    """Choose the highest-precision threshold meeting a validation recall target."""

    if not 0.0 < target_recall <= 1.0:
        raise ValueError("target_recall must be in (0, 1].")
    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    valid = np.flatnonzero(recall[:-1] >= target_recall)
    if thresholds.size == 0 or valid.size == 0:
        return threshold_at_best_f1(y_true, probabilities)
    best_index = int(valid[np.argmax(precision[:-1][valid])])
    return float(np.clip(thresholds[best_index], 0.0, 1.0))


def derive_thresholds(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    target_fpr: float,
    review_capacity: int,
    review_target_recall: float = 0.90,
) -> DecisionThresholds:
    """Fit block risk tolerance and manual-review capacity on validation data."""

    if len(y_true) != len(probabilities) or len(y_true) == 0:
        raise ValueError("Labels and probabilities must be non-empty and aligned.")
    review = threshold_at_target_recall(
        y_true, probabilities, review_target_recall
    )
    block_at_fpr = threshold_at_fixed_fpr(y_true, probabilities, target_fpr)
    k = min(max(review_capacity, 1), len(probabilities))

    # Reserve the highest-risk quarter of measured investigator capacity for
    # automatic blocks. Precision@K still measures capacity, but the review cutoff
    # is learned from validation precision/recall so ambiguous cases are not
    # silently approved merely because a batch happened to be large.
    block_k = min(max(k // 4, 1), len(probabilities))
    block_at_capacity = float(
        np.partition(probabilities, len(probabilities) - block_k)[-block_k]
    )
    block = max(block_at_fpr, block_at_capacity)
    if block <= review:
        block = float(np.nextafter(review, 1.0))
    block = min(block, 1.0)
    return DecisionThresholds(
        review=review,
        block=block,
        target_fpr=target_fpr,
        review_capacity=review_capacity,
        review_target_recall=review_target_recall,
    )


def make_decisions(
    probabilities: np.ndarray, thresholds: DecisionThresholds
) -> np.ndarray:
    """Convert probabilities to Approve, Manually Review, or Block."""

    values = np.asarray(probabilities, dtype=float)
    return np.select(
        [values >= thresholds.block, values >= thresholds.review],
        ["Block", "Manually Review"],
        default="Approve",
    )


def make_capacity_aware_decisions(
    probabilities: np.ndarray, thresholds: DecisionThresholds
) -> np.ndarray:
    """Apply thresholds while admitting at most the highest-risk capacity to review.

    Automatic blocks do not consume investigator capacity. Review candidates below
    the capacity cutline become approvals with a separately reportable suppression
    count; serving applies the equivalent policy to the active PostgreSQL queue.
    """

    values = np.asarray(probabilities, dtype=float)
    decisions = np.full(values.shape, "Approve", dtype=object)
    blocked = values >= thresholds.block
    decisions[blocked] = "Block"
    candidates = np.flatnonzero(
        (values >= thresholds.review) & ~blocked
    )
    if candidates.size:
        capacity = min(thresholds.review_capacity, candidates.size)
        ranked = candidates[np.argsort(values[candidates], kind="stable")[::-1]]
        decisions[ranked[:capacity]] = "Manually Review"
    return decisions.astype(str)
