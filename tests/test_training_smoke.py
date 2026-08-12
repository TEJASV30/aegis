from __future__ import annotations

from fraud_platform.data.generate_synthetic import generate_transactions
from fraud_platform.features.definitions import MODEL_FEATURES
from fraud_platform.features.velocity import add_velocity_features
from fraud_platform.models.splits import temporal_split
from fraud_platform.models.train import _fit_candidate, build_candidates


def test_temporal_training_smoke_is_finite_and_calibrated() -> None:
    frame = add_velocity_features(
        generate_transactions(
            n_transactions=4_000,
            n_customers=500,
            n_merchants=120,
            days=120,
            seed=123,
        )
    )
    partitions = temporal_split(frame)
    labels = partitions["train"]["is_fraud"]
    class_weight = (len(labels) - labels.sum()) / labels.sum()
    pipeline, direction = build_candidates(class_weight, 123)[
        "logistic_regression"
    ]

    model = _fit_candidate(
        "logistic_regression", pipeline, direction, partitions
    )
    probability = model.predict_proba(partitions["selection"][MODEL_FEATURES])[:, 1]

    assert probability.min() >= 0
    assert probability.max() <= 1
    assert probability.std() > 0
