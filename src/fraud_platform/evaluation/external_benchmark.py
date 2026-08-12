"""Evaluate required candidates on the public OpenML credit-card benchmark."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.datasets import fetch_openml
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from fraud_platform.models.decision import derive_thresholds
from fraud_platform.models.evaluation import bootstrap_intervals
from fraud_platform.models.metrics import evaluate_predictions

OPENML_DATA_ID = 1597
BENCHMARK_NAME = "CreditCardFraudDetection"


def _raw_score(model: Any, frame: pd.DataFrame, direction: float) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        probability = np.asarray(model.predict_proba(frame)[:, 1], dtype=float)
        return np.log(np.clip(probability, 1e-6, 1 - 1e-6) / np.clip(1 - probability, 1e-6, 1))
    return direction * np.asarray(model.decision_function(frame), dtype=float)


def _partitions(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Create ordered partitions with an untouched final source period.

    OpenML data ID 1597 omits the original elapsed-time field. The server retains
    source row order, so this benchmark preserves that order and labels the
    limitation explicitly instead of presenting the split as timestamp-based.
    """

    order_columns = ["Time", "__row_id"] if "Time" in frame else ["__row_id"]
    ordered = frame.sort_values(order_columns, kind="mergesort").reset_index(drop=True)
    bounds = [0, int(len(ordered) * 0.60), int(len(ordered) * 0.75), int(len(ordered) * 0.85), len(ordered)]
    names = ["train", "calibration", "selection", "test"]
    return {
        name: ordered.iloc[bounds[index] : bounds[index + 1]].copy()
        for index, name in enumerate(names)
    }


def run_benchmark(
    output: Path,
    data_home: Path,
    *,
    seed: int = 42,
    target_fpr: float = 0.01,
    review_capacity: int = 500,
    bootstrap_iterations: int = 200,
) -> dict[str, Any]:
    """Download, temporally evaluate, calibrate, and report a public benchmark."""

    dataset = fetch_openml(
        data_id=OPENML_DATA_ID,
        as_frame=True,
        data_home=data_home,
        parser="auto",
    )
    frame = dataset.frame.copy()
    required = {"Class", "Amount"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"OpenML data ID {OPENML_DATA_ID} is missing required columns: "
            f"{sorted(missing)}"
        )
    frame["Class"] = pd.to_numeric(frame["Class"], errors="raise").astype(int)
    frame["__row_id"] = np.arange(len(frame))
    feature_names = [column for column in frame.columns if column not in {"Class", "__row_id"}]
    partitions = _partitions(frame)
    positive = int(partitions["train"]["Class"].sum())
    class_weight = (len(partitions["train"]) - positive) / max(positive, 1)
    candidates: dict[str, tuple[Any, float]] = {
        "logistic_regression": (
            make_pipeline(
                StandardScaler(),
                LogisticRegression(class_weight="balanced", max_iter=2_000, random_state=seed),
            ),
            1.0,
        ),
        "isolation_forest": (
            make_pipeline(
                StandardScaler(),
                IsolationForest(n_estimators=250, n_jobs=-1, random_state=seed),
            ),
            -1.0,
        ),
        "xgboost": (
            XGBClassifier(
                objective="binary:logistic",
                eval_metric="aucpr",
                n_estimators=300,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.85,
                colsample_bytree=0.85,
                scale_pos_weight=class_weight,
                n_jobs=-1,
                random_state=seed,
            ),
            1.0,
        ),
        "lightgbm": (
            LGBMClassifier(
                objective="binary",
                n_estimators=300,
                learning_rate=0.05,
                num_leaves=31,
                scale_pos_weight=class_weight,
                n_jobs=-1,
                random_state=seed,
                verbosity=-1,
            ),
            1.0,
        ),
    }
    results: dict[str, Any] = {}
    for name, (model, direction) in candidates.items():
        train = partitions["train"]
        if name == "isolation_forest":
            model.fit(train.loc[train["Class"] == 0, feature_names])
        else:
            model.fit(train[feature_names], train["Class"])
        calibration = partitions["calibration"]
        calibration_score = _raw_score(model, calibration[feature_names], direction)
        calibrator = LogisticRegression(random_state=seed)
        calibrator.fit(calibration_score.reshape(-1, 1), calibration["Class"])
        selection = partitions["selection"]
        selection_probability = calibrator.predict_proba(
            _raw_score(model, selection[feature_names], direction).reshape(-1, 1)
        )[:, 1]
        thresholds = derive_thresholds(
            selection["Class"].to_numpy(dtype=int),
            selection_probability,
            target_fpr,
            review_capacity,
        )
        test = partitions["test"]
        test_probability = calibrator.predict_proba(
            _raw_score(model, test[feature_names], direction).reshape(-1, 1)
        )[:, 1]
        metrics, metadata = evaluate_predictions(
            test["Class"].to_numpy(dtype=int),
            test_probability,
            test["Amount"].to_numpy(dtype=float),
            thresholds,
            review_capacity,
        )
        results[name] = {
            "metrics": metrics,
            "thresholds": thresholds.to_dict(),
            "bootstrap_95_intervals": bootstrap_intervals(
                test["Class"].to_numpy(dtype=int),
                test_probability,
                target_fpr=target_fpr,
                iterations=bootstrap_iterations,
                seed=seed,
            ),
            "simulation_metadata": metadata,
        }
    payload = {
        "benchmark": BENCHMARK_NAME,
        "openml_data_id": OPENML_DATA_ID,
        "source_url": f"https://www.openml.org/d/{OPENML_DATA_ID}",
        "license": "Database Contents License (DbCL) 1.0",
        "measured_at": datetime.now(UTC).isoformat(),
        "split_policy": (
            "first 60% train, next 15% calibration, next 10% threshold "
            "selection, final 15% untouched test; source row order preserved "
            "because OpenML data ID 1597 omits the original Time field"
        ),
        "ordering_field": "source_row_order",
        "rows": len(frame),
        "fraud_rows": int(frame["Class"].sum()),
        "test_rows": len(partitions["test"]),
        "test_fraud_rows": int(partitions["test"]["Class"].sum()),
        "results": results,
        "limitations": [
            "The public data is anonymized and contains no customer/device identity for velocity parity testing.",
            "OpenML data ID 1597 omits the original elapsed-time field; this evaluation preserves source row order but cannot prove a timestamp-based split.",
            "Monetary loss remains a policy simulation, not observed causal savings.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/external_benchmark.json"))
    parser.add_argument("--data-home", type=Path, default=Path("data/external/openml"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bootstrap-iterations", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run_benchmark(
        args.output,
        args.data_home,
        seed=args.seed,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
