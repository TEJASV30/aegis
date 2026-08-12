from __future__ import annotations

import numpy as np
import pandas as pd

from fraud_platform.models.decision import (
    DecisionThresholds,
    derive_thresholds,
    make_decisions,
    threshold_at_best_f1,
    threshold_at_target_recall,
)
from fraud_platform.models.metrics import (
    evaluate_predictions,
    expected_calibration_error,
    precision_at_k,
    simulate_monetary_loss,
)
from fraud_platform.models.splits import temporal_split


def test_decision_mapping_has_three_outcomes() -> None:
    thresholds = DecisionThresholds(
        review=0.30, block=0.80, target_fpr=0.01, review_capacity=10
    )
    decisions = make_decisions(np.array([0.1, 0.4, 0.9]), thresholds)
    assert decisions.tolist() == ["Approve", "Manually Review", "Block"]


def test_derived_thresholds_preserve_a_manual_review_band() -> None:
    labels = np.array([0] * 90 + [1] * 10)
    probabilities = np.linspace(0.001, 0.999, 100)

    thresholds = derive_thresholds(
        labels, probabilities, target_fpr=0.01, review_capacity=20
    )
    decisions = make_decisions(probabilities, thresholds)

    assert thresholds.review < thresholds.block
    assert {"Approve", "Manually Review", "Block"}.issubset(set(decisions))


def test_review_threshold_uses_precision_recall_not_only_capacity() -> None:
    labels = np.array([0, 0, 0, 1, 1, 1])
    probabilities = np.array([0.01, 0.08, 0.30, 0.45, 0.72, 0.95])

    threshold = threshold_at_best_f1(labels, probabilities)

    assert threshold == 0.45


def test_review_threshold_can_target_edge_case_recall() -> None:
    labels = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    probabilities = np.array([0.01, 0.04, 0.10, 0.30, 0.08, 0.45, 0.72, 0.95])

    threshold = threshold_at_target_recall(
        labels, probabilities, target_recall=0.90
    )

    assert threshold <= 0.08


def test_operational_metrics_are_well_formed() -> None:
    labels = np.array([0, 0, 1, 1])
    probabilities = np.array([0.05, 0.10, 0.70, 0.95])
    assert precision_at_k(labels, probabilities, 2) == 1.0
    assert 0.0 <= expected_calibration_error(labels, probabilities) <= 1.0
    result = simulate_monetary_loss(
        labels,
        np.array([10.0, 20.0, 100.0, 200.0]),
        probabilities,
        DecisionThresholds(0.5, 0.9, 0.01, 2),
    )
    assert result["baseline_approve_all_fraud_loss"] == 300.0
    assert "simulated_net_monetary_loss_avoided" in result


def test_evaluation_reports_accuracy_and_fraud_sensitive_metrics() -> None:
    labels = np.array([0, 0, 0, 0, 1, 1])
    probabilities = np.array([0.01, 0.04, 0.08, 0.20, 0.78, 0.95])
    thresholds = DecisionThresholds(0.20, 0.75, 0.01, 2)

    metrics, _ = evaluate_predictions(
        labels,
        probabilities,
        np.array([10.0, 20.0, 15.0, 40.0, 100.0, 200.0]),
        thresholds,
        top_k=2,
    )

    assert metrics["accuracy"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["fraud_precision"] == 1.0
    assert metrics["fraud_recall"] == 1.0
    assert metrics["pr_auc_average_precision"] == 1.0


def test_temporal_split_boundaries_are_strict() -> None:
    count = 1_000
    frame = pd.DataFrame(
        {
            "event_timestamp": pd.date_range(
                "2025-01-01", periods=count, freq="h", tz="UTC"
            ),
            "is_fraud": np.arange(count) % 7 == 0,
        }
    )
    partitions = temporal_split(frame)
    names = ["train", "calibration", "selection", "test"]
    for left, right in zip(names[:-1], names[1:], strict=True):
        assert (
            partitions[left]["event_timestamp"].max()
            < partitions[right]["event_timestamp"].min()
        )
