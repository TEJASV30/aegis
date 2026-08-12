"""Fraud metrics centered on operations, calibration, and money."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_curve,
)

from fraud_platform.models.decision import (
    DecisionThresholds,
    make_capacity_aware_decisions,
    make_decisions,
)


@dataclass(frozen=True)
class LossAssumptions:
    """Transparent assumptions for the counterfactual loss simulation."""

    fraud_recovery_rate_if_blocked: float = 1.0
    fraud_recovery_rate_if_reviewed: float = 0.80
    review_operating_cost: float = 5.0
    legitimate_review_friction_cost: float = 3.0
    legitimate_block_friction_cost: float = 25.0


def expected_calibration_error(
    y_true: np.ndarray, probabilities: np.ndarray, n_bins: int = 10
) -> float:
    """Calculate equal-width Expected Calibration Error (ECE)."""

    boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    bin_index = np.clip(np.digitize(probabilities, boundaries[1:-1]), 0, n_bins - 1)
    ece = 0.0
    for index in range(n_bins):
        mask = bin_index == index
        if mask.any():
            ece += mask.mean() * abs(y_true[mask].mean() - probabilities[mask].mean())
    return float(ece)


def recall_at_fixed_fpr(
    y_true: np.ndarray, probabilities: np.ndarray, target_fpr: float
) -> float:
    """Return maximum recall available without exceeding the specified FPR."""

    fpr, tpr, _ = roc_curve(y_true, probabilities)
    valid = tpr[fpr <= target_fpr]
    return float(valid.max()) if valid.size else 0.0


def precision_at_k(y_true: np.ndarray, probabilities: np.ndarray, k: int) -> float:
    """Measure precision in the highest-risk K transactions."""

    if len(y_true) == 0:
        return 0.0
    effective_k = min(max(k, 1), len(y_true))
    top_indices = np.argpartition(probabilities, -effective_k)[-effective_k:]
    return float(np.asarray(y_true)[top_indices].mean())


def simulate_monetary_loss(
    y_true: np.ndarray,
    amounts: np.ndarray,
    probabilities: np.ndarray,
    thresholds: DecisionThresholds,
    assumptions: LossAssumptions | None = None,
) -> dict[str, float]:
    """Estimate avoided loss versus approving every transaction.

    This is an explicit decision simulation, not a historical causal estimate.
    Values use the versioned canonical synthetic USD amount.
    """

    assumptions = assumptions or LossAssumptions()
    labels = np.asarray(y_true, dtype=int)
    values = np.asarray(amounts, dtype=float)
    decisions = make_capacity_aware_decisions(probabilities, thresholds)
    fraud = labels == 1
    legitimate = ~fraud
    reviewed = decisions == "Manually Review"
    blocked = decisions == "Block"
    approved = decisions == "Approve"

    baseline_fraud_loss = float(values[fraud].sum())
    residual_fraud_loss = float(values[fraud & approved].sum())
    residual_fraud_loss += float(
        values[fraud & reviewed].sum()
        * (1.0 - assumptions.fraud_recovery_rate_if_reviewed)
    )
    residual_fraud_loss += float(
        values[fraud & blocked].sum()
        * (1.0 - assumptions.fraud_recovery_rate_if_blocked)
    )
    review_cost = float(reviewed.sum() * assumptions.review_operating_cost)
    friction_cost = float(
        (legitimate & reviewed).sum() * assumptions.legitimate_review_friction_cost
        + (legitimate & blocked).sum() * assumptions.legitimate_block_friction_cost
    )
    policy_loss = residual_fraud_loss + review_cost + friction_cost
    return {
        "baseline_approve_all_fraud_loss": baseline_fraud_loss,
        "policy_residual_fraud_loss": residual_fraud_loss,
        "policy_review_operating_cost": review_cost,
        "policy_legitimate_friction_cost": friction_cost,
        "policy_total_simulated_loss": policy_loss,
        "simulated_net_monetary_loss_avoided": baseline_fraud_loss - policy_loss,
    }


def evaluate_predictions(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    amounts: np.ndarray,
    thresholds: DecisionThresholds,
    top_k: int,
    assumptions: LossAssumptions | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Calculate and return all required decisioning metrics and assumptions."""

    assumptions = assumptions or LossAssumptions()
    labels = np.asarray(y_true, dtype=int)
    binary_predictions = (np.asarray(probabilities) >= 0.5).astype(int)
    decisions = make_capacity_aware_decisions(probabilities, thresholds)
    policy_risk_predictions = (decisions != "Approve").astype(int)
    uncapped_decisions = make_decisions(probabilities, thresholds)
    metrics = {
        "accuracy": float(accuracy_score(labels, binary_predictions)),
        "balanced_accuracy": float(
            balanced_accuracy_score(labels, binary_predictions)
        ),
        "fraud_precision": float(
            precision_score(labels, binary_predictions, zero_division=0)
        ),
        "fraud_recall": float(
            recall_score(labels, binary_predictions, zero_division=0)
        ),
        "fraud_f1": float(f1_score(labels, binary_predictions, zero_division=0)),
        "policy_accuracy": float(
            accuracy_score(labels, policy_risk_predictions)
        ),
        "policy_fraud_precision": float(
            precision_score(labels, policy_risk_predictions, zero_division=0)
        ),
        "policy_fraud_recall": float(
            recall_score(labels, policy_risk_predictions, zero_division=0)
        ),
        "pr_auc_average_precision": float(
            average_precision_score(labels, probabilities)
        ),
        "recall_at_fixed_fpr": recall_at_fixed_fpr(
            labels, probabilities, thresholds.target_fpr
        ),
        "precision_at_k": precision_at_k(labels, probabilities, top_k),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "expected_calibration_error": expected_calibration_error(
            labels, probabilities
        ),
        "review_count": float((decisions == "Manually Review").sum()),
        "review_capacity": float(thresholds.review_capacity),
        "review_candidates_before_capacity": float(
            (uncapped_decisions == "Manually Review").sum()
        ),
        "review_suppressed_by_capacity": float(
            (
                (uncapped_decisions == "Manually Review")
                & (decisions == "Approve")
            ).sum()
        ),
    }
    metrics.update(
        simulate_monetary_loss(
            y_true, amounts, probabilities, thresholds, assumptions
        )
    )
    return metrics, {"loss_simulation_assumptions": asdict(assumptions)}
