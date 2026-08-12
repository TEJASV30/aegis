from __future__ import annotations

from pathlib import Path

import pytest


def test_airflow_dags_load_and_encode_release_dependencies() -> None:
    airflow = pytest.importorskip("airflow")
    del airflow
    from airflow.models import DagBag

    dag_path = Path(__file__).parents[1] / "airflow" / "dags"
    bag = DagBag(dag_folder=str(dag_path), include_examples=False)

    assert bag.import_errors == {}
    training = bag.get_dag("fraud_model_training")
    assert training is not None
    expected = {
        "validate_source",
        "build_features",
        "validate_feature_parity",
        "train_challenger",
        "validate_challenger",
        "promote_champion",
    }
    assert expected.issubset(training.task_ids)
    assert "validate_challenger" in training.get_task("promote_champion").upstream_task_ids
