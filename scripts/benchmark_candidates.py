"""Run a fast, local temporal benchmark without writing production artifacts."""

from __future__ import annotations

import argparse
import json

from fraud_platform.data.generate_synthetic import generate_transactions
from fraud_platform.features.definitions import MODEL_FEATURES
from fraud_platform.features.velocity import add_velocity_features
from fraud_platform.models.decision import derive_thresholds
from fraud_platform.models.metrics import evaluate_predictions
from fraud_platform.models.splits import temporal_split
from fraud_platform.models.train import _fit_candidate, build_candidates

REPORTED_METRICS = [
    "accuracy",
    "balanced_accuracy",
    "fraud_precision",
    "fraud_recall",
    "pr_auc_average_precision",
    "recall_at_fixed_fpr",
    "precision_at_k",
    "brier_score",
    "expected_calibration_error",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    customers = min(8_000, max(500, args.rows // 10))
    merchants = min(1_500, max(100, args.rows // 50))
    frame = add_velocity_features(
        generate_transactions(
            args.rows,
            customers,
            merchants,
            days=120,
            seed=args.seed,
        )
    )
    partitions = temporal_split(frame)
    train_target = partitions["train"]["is_fraud"].to_numpy(dtype=int)
    positive_count = max(int(train_target.sum()), 1)
    class_weight = (len(train_target) - positive_count) / positive_count
    selection = partitions["selection"]
    selection_target = selection["is_fraud"].to_numpy(dtype=int)
    results: dict[str, dict[str, float]] = {}

    for name, (pipeline, direction) in build_candidates(
        float(class_weight), args.seed
    ).items():
        model = _fit_candidate(name, pipeline, direction, partitions)
        probabilities = model.predict_proba(selection[MODEL_FEATURES])[:, 1]
        thresholds = derive_thresholds(
            selection_target, probabilities, target_fpr=0.01, review_capacity=500
        )
        metrics, _ = evaluate_predictions(
            selection_target,
            probabilities,
            selection["amount"].to_numpy(dtype=float),
            thresholds,
            top_k=500,
        )
        results[name] = {
            metric: round(metrics[metric], 6) for metric in REPORTED_METRICS
        }

    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
