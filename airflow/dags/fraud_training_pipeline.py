"""Airflow orchestration for synthetic ingestion, features, training, and drift."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta

from airflow.decorators import dag, task

DATA_DIR = os.getenv("FRAUD_DATA_DIR", "/opt/airflow/data")
CANDIDATE_DIR = os.getenv(
    "CANDIDATE_MODEL_DIR", "/opt/airflow/artifacts/candidate"
)
RELEASE_DIR = os.getenv("MODEL_DIR", "/opt/airflow/artifacts/model")
REPORT_DIR = os.getenv("FRAUD_REPORT_DIR", "/opt/airflow/reports/drift")
DEMO_MODE = os.getenv("AEGIS_AIRFLOW_DEMO_MODE", "false").lower() in {
    "1",
    "true",
    "yes",
}
TRAINING_SCHEDULE = None if DEMO_MODE else "0 2 * * 0"
DRIFT_SCHEDULE = None if DEMO_MODE else "30 3 * * *"


def _run_module(module: str, *arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    completed.check_returncode()


@dag(
    dag_id="fraud_synthetic_bootstrap",
    description="Generate a labeled local transaction history in PostgreSQL",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "fraud-ml-platform",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["fraud", "synthetic-data", "bootstrap"],
)
def fraud_synthetic_bootstrap() -> None:
    """Provide an Airflow alternative to the website's synthetic-data action."""

    @task
    def generate_synthetic() -> str:
        output = f"{DATA_DIR}/transactions.parquet"
        _run_module(
            "fraud_platform.data.generate_synthetic",
            "--rows",
            os.getenv("SYNTHETIC_ROWS", "100000"),
            "--output",
            output,
            "--write-postgres",
        )
        return output

    generate_synthetic()


@dag(
    dag_id="fraud_model_training",
    description="Point-in-time feature generation and temporal model selection",
    start_date=datetime(2025, 1, 1),
    schedule=TRAINING_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "fraud-ml-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["fraud", "ml", "open-source"],
)
def fraud_model_training() -> None:
    """Validate, train, register, gate and promote the weekly challenger."""

    @task
    def validate_source() -> str:
        _run_module("fraud_platform.data.validate")
        return "source-passed"

    @task
    def build_features(source_gate: str) -> str:
        del source_gate
        output = f"{DATA_DIR}/features.parquet"
        _run_module(
            "fraud_platform.features.build_features",
            "--read-postgres",
            "--output",
            output,
            "--write-postgres",
        )
        return output

    @task
    def validate_feature_parity(feature_path: str) -> str:
        del feature_path
        _run_module(
            "fraud_platform.features.parity",
            "--history-limit",
            "5000",
            "--sample-size",
            "25",
        )
        return "parity-passed"

    @task
    def train_challenger(feature_path: str, parity_gate: str) -> str:
        del parity_gate
        _run_module(
            "fraud_platform.models.train",
            "--input",
            feature_path,
            "--output-dir",
            CANDIDATE_DIR,
        )
        return f"{CANDIDATE_DIR}/manifest.json"

    @task
    def validate_challenger(manifest_path: str) -> str:
        del manifest_path
        _run_module(
            "fraud_platform.models.validate_release",
            "--candidate-dir",
            CANDIDATE_DIR,
        )
        return "challenger-passed"

    @task
    def promote_champion(validation_gate: str) -> str:
        del validation_gate
        _run_module(
            "fraud_platform.models.promote",
            "--candidate-dir",
            CANDIDATE_DIR,
            "--release-root",
            RELEASE_DIR,
        )
        return f"{RELEASE_DIR}/current.json"

    source = validate_source()
    features = build_features(source)
    parity = validate_feature_parity(features)
    candidate = train_challenger(features, parity)
    validated = validate_challenger(candidate)
    promote_champion(validated)


@dag(
    dag_id="fraud_production_drift",
    description="Daily feature drift and distribution snapshots",
    start_date=datetime(2025, 1, 1),
    schedule=DRIFT_SCHEDULE,
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "fraud-ml-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["fraud", "monitoring", "evidently"],
)
def fraud_production_drift() -> None:
    """Compare the current production window with the offline reference."""

    @task
    def calculate_drift() -> None:
        _run_module(
            "fraud_platform.monitoring.drift",
            "--reference",
            f"{DATA_DIR}/features.parquet",
            "--output-dir",
            REPORT_DIR,
            "--current-days",
            "1",
        )

    @task
    def evaluate_matured_outcomes() -> None:
        _run_module(
            "fraud_platform.monitoring.performance",
            "--lookback-days",
            "90",
        )

    calculate_drift()
    evaluate_matured_outcomes()


fraud_synthetic_bootstrap()
fraud_model_training()
fraud_production_drift()
