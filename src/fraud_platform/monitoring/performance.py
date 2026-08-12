"""Monitor matured-label quality by active model and label cohort."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb
from sklearn.metrics import average_precision_score, brier_score_loss

from fraud_platform.config import get_settings
from fraud_platform.db import database_connection
from fraud_platform.models.metrics import (
    expected_calibration_error,
    recall_at_fixed_fpr,
)


def _load_matured(cutoff: datetime, lookback_days: int) -> pd.DataFrame:
    start = cutoff - timedelta(days=lookback_days)
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT model_version, feature_version, scored_at, outcome_recorded_at,
                   actual_is_fraud, fraud_probability, decision,
                   review_threshold, block_threshold,
                   (features->>'amount_usd')::DOUBLE PRECISION AS amount_usd
            FROM production_predictions
            WHERE actual_is_fraud IS NOT NULL
              AND outcome_recorded_at IS NOT NULL
              AND outcome_recorded_at <= %s
              AND scored_at >= %s
              AND scored_at < %s
            ORDER BY scored_at
            """,
            (cutoff, start, cutoff),
        ).fetchall()
    return pd.DataFrame.from_records(rows)


def _metrics(group: pd.DataFrame) -> dict[str, Any]:
    labels = group["actual_is_fraud"].to_numpy(dtype=int)
    probabilities = group["fraud_probability"].to_numpy(dtype=float)
    decisions = group["decision"].astype(str)
    both_classes = np.unique(labels).size == 2
    return {
        "pr_auc_average_precision": (
            float(average_precision_score(labels, probabilities))
            if both_classes
            else None
        ),
        "recall_at_fixed_fpr": (
            recall_at_fixed_fpr(labels, probabilities, 0.01)
            if both_classes
            else None
        ),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "expected_calibration_error": expected_calibration_error(
            labels, probabilities
        ),
        "approval_yield": float((decisions == "Approve").mean()),
        "review_yield": float((decisions == "Manually Review").mean()),
        "block_yield": float((decisions == "Block").mean()),
        "prediction_mean": float(probabilities.mean()),
        "prediction_p50": float(np.quantile(probabilities, 0.50)),
        "prediction_p95": float(np.quantile(probabilities, 0.95)),
        "observed_fraud_rate": float(labels.mean()),
    }


def _alert_status(metrics: dict[str, Any], sample_count: int, fraud_count: int) -> str:
    if sample_count < 100 or fraud_count < 20:
        return "INSUFFICIENT_MATURE_LABELS"
    failures = [
        metrics["pr_auc_average_precision"] is not None
        and metrics["pr_auc_average_precision"] < 0.65,
        metrics["recall_at_fixed_fpr"] is not None
        and metrics["recall_at_fixed_fpr"] < 0.65,
        metrics["expected_calibration_error"] > 0.10,
    ]
    return "ALERT" if any(failures) else "HEALTHY"


def monitor_performance(
    maturity_hours: int,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Persist evidence only after labels pass the configured maturity delay."""

    generated_at = datetime.now(UTC)
    cutoff = generated_at - timedelta(hours=maturity_hours)
    frame = _load_matured(cutoff, lookback_days)
    if frame.empty:
        return {
            "status": "INSUFFICIENT_MATURE_LABELS",
            "maturity_cutoff": cutoff.isoformat(),
            "snapshots": [],
        }
    snapshots: list[dict[str, Any]] = []
    with database_connection() as connection:
        for (model_version, feature_version), group in frame.groupby(
            ["model_version", "feature_version"], observed=True
        ):
            metrics = _metrics(group)
            sample_count = len(group)
            fraud_count = int(group["actual_is_fraud"].sum())
            status = _alert_status(metrics, sample_count, fraud_count)
            snapshot_id = uuid.uuid4()
            evidence = {
                "snapshot_id": str(snapshot_id),
                "model_version": str(model_version),
                "feature_version": str(feature_version),
                "sample_count": sample_count,
                "fraud_count": fraud_count,
                "cohort_start": group["scored_at"].min().isoformat(),
                "cohort_end": group["scored_at"].max().isoformat(),
                "maturity_cutoff": cutoff.isoformat(),
                "metrics": metrics,
                "alert_status": status,
            }
            connection.execute(
                """
                INSERT INTO model_performance_snapshots (
                    snapshot_id, model_version, feature_version, cohort_start,
                    cohort_end, maturity_cutoff, sample_count, fraud_count,
                    metrics, alert_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot_id,
                    model_version,
                    feature_version,
                    group["scored_at"].min(),
                    group["scored_at"].max(),
                    cutoff,
                    sample_count,
                    fraud_count,
                    Jsonb(metrics),
                    status,
                ),
            )
            if status == "ALERT":
                connection.execute(
                    """
                    INSERT INTO monitoring_alerts (
                        alert_id, alert_type, severity, model_version,
                        message, evidence
                    ) VALUES (%s, 'MATURED_MODEL_PERFORMANCE', 'HIGH', %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        model_version,
                        "Matured-label quality breached one or more reviewed gates; no automatic promotion occurred.",
                        Jsonb(evidence),
                    ),
                )
            snapshots.append(evidence)
    return {
        "status": "completed",
        "maturity_cutoff": cutoff.isoformat(),
        "snapshots": snapshots,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maturity-hours", type=int, default=get_settings().label_maturity_hours
    )
    parser.add_argument("--lookback-days", type=int, default=90)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            monitor_performance(args.maturity_hours, args.lookback_days), indent=2
        )
    )


if __name__ == "__main__":
    main()
