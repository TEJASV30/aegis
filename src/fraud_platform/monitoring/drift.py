"""Generate Evidently drift reports and PostgreSQL distribution snapshots."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report
from psycopg.types.json import Jsonb
from scipy.stats import ks_2samp

from fraud_platform.db import database_connection
from fraud_platform.features.definitions import CATEGORICAL_FEATURES, MODEL_FEATURES


def load_current_features(start: datetime, end: datetime) -> pd.DataFrame:
    """Read production feature documents for a closed-open time interval."""

    query = """
        SELECT features
        FROM production_predictions
        WHERE scored_at >= %s AND scored_at < %s
        ORDER BY scored_at
    """
    with database_connection() as connection:
        rows = connection.execute(query, (start, end)).fetchall()
    return pd.DataFrame([dict(row["features"]) for row in rows], columns=MODEL_FEATURES)


def _stable_drift_summary(
    reference: pd.DataFrame, current: pd.DataFrame
) -> dict[str, Any]:
    """Calculate auditable per-feature drift alongside Evidently's report."""

    feature_metrics: dict[str, dict[str, Any]] = {}
    for feature in MODEL_FEATURES:
        reference_values = reference[feature].dropna()
        current_values = current[feature].dropna()
        if feature in CATEGORICAL_FEATURES:
            categories = sorted(
                set(reference_values.astype(str)) | set(current_values.astype(str))
            )
            reference_frequency = (
                reference_values.astype(str).value_counts(normalize=True).reindex(
                    categories, fill_value=0.0
                )
            )
            current_frequency = (
                current_values.astype(str).value_counts(normalize=True).reindex(
                    categories, fill_value=0.0
                )
            )
            score = float(0.5 * np.abs(reference_frequency - current_frequency).sum())
            drifted = score >= 0.10
            feature_metrics[feature] = {
                "method": "total_variation_distance",
                "score": score,
                "threshold": 0.10,
                "drifted": drifted,
            }
        else:
            if min(len(reference_values), len(current_values)) < 20:
                score, p_value, drifted = 0.0, 1.0, False
            else:
                result = ks_2samp(reference_values, current_values)
                score, p_value = float(result.statistic), float(result.pvalue)
                drifted = p_value < 0.05 and score >= 0.10
            feature_metrics[feature] = {
                "method": "kolmogorov_smirnov",
                "score": score,
                "p_value": p_value,
                "thresholds": {"p_value": 0.05, "effect_size": 0.10},
                "drifted": drifted,
            }
    drifted_count = sum(int(item["drifted"]) for item in feature_metrics.values())
    return {
        "dataset_drift": drifted_count / len(MODEL_FEATURES) >= 0.20,
        "drifted_feature_count": drifted_count,
        "total_feature_count": len(MODEL_FEATURES),
        "features": feature_metrics,
    }


def persist_feature_distributions(
    current: pd.DataFrame, start: datetime, end: datetime, snapshot_id: uuid.UUID
) -> None:
    """Persist numeric quantiles or categorical frequencies for Superset."""

    query = """
        INSERT INTO feature_distribution_snapshots (
            snapshot_id, window_start, window_end, feature_name, sample_count,
            missing_rate, mean_value, std_value, p01_value, p50_value, p99_value,
            category_frequencies
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_id, feature_name) DO NOTHING
    """
    records: list[tuple[Any, ...]] = []
    for feature in MODEL_FEATURES:
        series = current[feature]
        missing_rate = float(series.isna().mean())
        if feature in CATEGORICAL_FEATURES:
            frequencies = series.astype("string").value_counts(normalize=True).to_dict()
            statistics: tuple[float | None, ...] = (None, None, None, None, None)
            categories: Jsonb | None = Jsonb(
                {str(key): float(value) for key, value in frequencies.items()}
            )
        else:
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            statistics = (
                float(numeric.mean()) if len(numeric) else None,
                float(numeric.std(ddof=0)) if len(numeric) else None,
                float(numeric.quantile(0.01)) if len(numeric) else None,
                float(numeric.quantile(0.50)) if len(numeric) else None,
                float(numeric.quantile(0.99)) if len(numeric) else None,
            )
            categories = None
        records.append(
            (
                snapshot_id,
                start,
                end,
                feature,
                int(series.notna().sum()),
                missing_rate,
                *statistics,
                categories,
            )
        )
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(query, records)


def run_drift_report(
    reference_path: Path,
    output_dir: Path,
    current_start: datetime,
    current_end: datetime,
) -> dict[str, Any]:
    """Compare the training reference with recent production traffic."""

    reference = pd.read_parquet(reference_path, columns=MODEL_FEATURES)
    current = load_current_features(current_start, current_end)
    if len(reference) < 100 or len(current) < 100:
        raise ValueError(
            "Reference and current datasets each require at least 100 rows."
        )
    reference = reference.sample(n=min(20_000, len(reference)), random_state=42)
    current = current.sample(n=min(20_000, len(current)), random_state=42)

    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current)
    summary = _stable_drift_summary(reference, current)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_id = uuid.uuid4()
    report_path = output_dir / f"drift_{report_id}.html"
    report.save_html(str(report_path))
    evidently_path = output_dir / f"drift_{report_id}.json"
    evidently_path.write_text(
        json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8"
    )

    reference_timestamp = pd.read_parquet(
        reference_path, columns=["event_timestamp"]
    )["event_timestamp"]
    reference_start = (
        pd.to_datetime(reference_timestamp, utc=True).min().to_pydatetime()
    )
    reference_end = pd.to_datetime(reference_timestamp, utc=True).max().to_pydatetime()
    persist_query = """
        INSERT INTO drift_reports (
            report_id, reference_start, reference_end, current_start, current_end,
            dataset_drift, drifted_feature_count, metrics, report_path
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with database_connection() as connection:
        connection.execute(
            persist_query,
            (
                report_id,
                reference_start,
                reference_end,
                current_start,
                current_end,
                summary["dataset_drift"],
                summary["drifted_feature_count"],
                Jsonb(summary),
                str(report_path),
            ),
        )
        if summary["dataset_drift"]:
            connection.execute(
                """
                INSERT INTO monitoring_alerts (
                    alert_id, alert_type, severity, message, evidence
                ) VALUES (%s, 'INPUT_DRIFT', 'MEDIUM', %s, %s)
                """,
                (
                    uuid.uuid4(),
                    "Input drift exceeded the reviewed dataset threshold; retraining was not triggered automatically.",
                    Jsonb(summary),
                ),
            )
    persist_feature_distributions(current, current_start, current_end, report_id)
    return {"report_id": str(report_id), "report_path": str(report_path), **summary}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference", type=Path, default=Path("data/features.parquet")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("reports/drift"))
    parser.add_argument("--current-days", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    """CLI entry point for the scheduled drift job."""

    args = parse_args()
    end = datetime.now(UTC)
    start = end - timedelta(days=args.current_days)
    summary = run_drift_report(args.reference, args.output_dir, start, end)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
