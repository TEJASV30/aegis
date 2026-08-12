"""Audit temporal holdout performance on ambiguous and risk-like edge cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix

from fraud_platform.features.definitions import MODEL_FEATURES
from fraud_platform.models.splits import temporal_split


def _segment_summary(
    mask: np.ndarray,
    labels: np.ndarray,
    probabilities: np.ndarray,
    decisions: np.ndarray,
) -> dict[str, Any]:
    count = int(mask.sum())
    if count == 0:
        return {"rows": 0}
    return {
        "rows": count,
        "observed_fraud_rate": float(labels[mask].mean()),
        "mean_probability": float(probabilities[mask].mean()),
        "median_probability": float(np.median(probabilities[mask])),
        "approve_rate": float((decisions[mask] == "Approve").mean()),
        "review_rate": float((decisions[mask] == "Manually Review").mean()),
        "block_rate": float((decisions[mask] == "Block").mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()

    frame = pd.read_parquet(args.features)
    test = temporal_split(frame)["test"]
    model = joblib.load(args.model)
    probabilities = model.predict_proba(test[MODEL_FEATURES])[:, 1]
    decisions = model.predict(test[MODEL_FEATURES])
    labels = test["is_fraud"].to_numpy(dtype=int)
    binary_predictions = (probabilities >= 0.5).astype(int)

    legitimate = labels == 0
    fraud = labels == 1
    risk_like_legitimate = legitimate & (
        (test["device_age_days"].to_numpy() < 10)
        | (
            (test["is_foreign"].to_numpy() == 1)
            & (test["distance_from_home_km"].to_numpy() > 150)
        )
        | (test["failed_attempts_24h"].to_numpy() >= 2)
        | (test["amount"].to_numpy() > 1_000)
    )

    segments = {
        "all_test": np.ones(len(test), dtype=bool),
        "legitimate": legitimate,
        "risk_like_legitimate": risk_like_legitimate,
        "all_fraud": fraud,
        "neutral_probability_20_to_80_percent": (
            (probabilities >= 0.20) & (probabilities <= 0.80)
        ),
        "manual_review_band": decisions == "Manually Review",
    }
    for fraud_type in ("account_takeover", "stolen_card", "friendly_fraud"):
        segments[fraud_type] = fraud & test["fraud_type"].eq(fraud_type).to_numpy()

    customer_age = test["customer_age"].to_numpy()
    age_bands = {
        "18_to_24": (customer_age >= 18) & (customer_age <= 24),
        "25_to_64": (customer_age >= 25) & (customer_age <= 64),
        "65_plus": customer_age >= 65,
    }
    for band_name, band_mask in age_bands.items():
        segments[f"age_{band_name}_legitimate"] = band_mask & legitimate
        segments[f"age_{band_name}_fraud"] = band_mask & fraud

    matrix = confusion_matrix(labels, binary_predictions, labels=[0, 1])
    output = {
        "model_name": model.model_name,
        "model_version": model.model_version,
        "test_rows": len(test),
        "accuracy_at_0_5": float(accuracy_score(labels, binary_predictions)),
        "confusion_matrix": {
            "true_negative": int(matrix[0, 0]),
            "false_positive": int(matrix[0, 1]),
            "false_negative": int(matrix[1, 0]),
            "true_positive": int(matrix[1, 1]),
        },
        "thresholds": model.thresholds.to_dict(),
        "segments": {
            name: _segment_summary(mask, labels, probabilities, decisions)
            for name, mask in segments.items()
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
