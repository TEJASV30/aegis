"""Train, calibrate, select, evaluate, and package fraud models."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import shap
from lightgbm import LGBMClassifier
from mlflow.models import infer_signature
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from fraud_platform.config import get_settings
from fraud_platform.features.definitions import (
    BASE_NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    FEATURE_VERSION,
    MODEL_FEATURES,
    NUMERIC_FEATURES,
    VELOCITY_FEATURES,
    feature_schema_fingerprint,
    feature_schema_payload,
)
from fraud_platform.models.artifacts import (
    CalibratedRiskModel,
    ShapExplainerArtifact,
    TransformedProbabilityFunction,
)
from fraud_platform.models.decision import derive_thresholds
from fraud_platform.models.evaluation import (
    bootstrap_intervals,
    calibration_comparison,
    segment_metrics,
)
from fraud_platform.models.metrics import LossAssumptions, evaluate_predictions
from fraud_platform.models.release import artifact_checksum
from fraud_platform.models.splits import temporal_split

LOGGER = logging.getLogger(__name__)


def _quality_gate_failures(
    metrics: dict[str, float], quality_gates: dict[str, float]
) -> dict[str, dict[str, float]]:
    """Return failed minimum model-quality requirements."""

    return {
        name: {"actual": metrics[name], "minimum": minimum}
        for name, minimum in quality_gates.items()
        if metrics[name] < minimum
    }


def build_preprocessor(feature_names: list[str] | None = None) -> ColumnTransformer:
    """Construct a stable mixed numeric/categorical preprocessing graph."""

    selected = feature_names or MODEL_FEATURES
    numeric_features = [name for name in NUMERIC_FEATURES if name in selected]
    categorical_features = [
        name for name in CATEGORICAL_FEATURES if name in selected
    ]

    numeric_pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler(with_mean=False)),
        ]
    )
    categorical_pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", sparse_output=True),
            ),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", numeric_pipeline, numeric_features),
            ("categorical", categorical_pipeline, categorical_features),
        ],
        sparse_threshold=0.3,
    )


def build_candidates(
    positive_class_weight: float,
    seed: int,
    feature_names: list[str] | None = None,
) -> dict[str, tuple[Any, float]]:
    """Return required baseline, anomaly, and gradient-boosting candidates."""

    estimators: dict[str, tuple[Any, float]] = {
        "logistic_regression": (
            LogisticRegression(
                class_weight="balanced",
                max_iter=2_000,
                solver="liblinear",
                random_state=seed,
            ),
            1.0,
        ),
        "isolation_forest": (
            IsolationForest(
                n_estimators=300,
                max_samples="auto",
                contamination="auto",
                n_jobs=-1,
                random_state=seed,
            ),
            -1.0,
        ),
        "xgboost": (
            XGBClassifier(
                objective="binary:logistic",
                eval_metric="aucpr",
                n_estimators=450,
                max_depth=6,
                learning_rate=0.05,
                min_child_weight=5,
                subsample=0.85,
                colsample_bytree=0.85,
                reg_lambda=2.0,
                scale_pos_weight=positive_class_weight,
                n_jobs=-1,
                random_state=seed,
            ),
            1.0,
        ),
        "lightgbm": (
            LGBMClassifier(
                objective="binary",
                n_estimators=450,
                num_leaves=31,
                learning_rate=0.05,
                min_child_samples=40,
                subsample=0.85,
                colsample_bytree=0.85,
                reg_lambda=2.0,
                scale_pos_weight=positive_class_weight,
                n_jobs=-1,
                random_state=seed,
                verbosity=-1,
            ),
            1.0,
        ),
    }
    return {
        name: (
            Pipeline(
                [
                    ("preprocess", build_preprocessor(feature_names)),
                    ("model", estimator),
                ]
            ),
            direction,
        )
        for name, (estimator, direction) in estimators.items()
    }


def _fit_candidate(
    name: str,
    pipeline: Pipeline,
    direction: float,
    partitions: dict[str, pd.DataFrame],
    feature_names: list[str] | None = None,
) -> CalibratedRiskModel:
    train = partitions["train"]
    selected_features = feature_names or MODEL_FEATURES
    x_train = train[selected_features]
    y_train = train["is_fraud"].to_numpy(dtype=int)
    if name == "isolation_forest":
        pipeline.fit(x_train.loc[y_train == 0])
    else:
        pipeline.fit(x_train, y_train)
    bundle = CalibratedRiskModel(
        model_name=name,
        estimator=pipeline,
        score_direction=direction,
        feature_names=selected_features,
        feature_version=FEATURE_VERSION,
        feature_schema_fingerprint=feature_schema_fingerprint(),
    )
    calibration = partitions["calibration"]
    bundle.fit_calibrator(
        calibration[selected_features], calibration["is_fraud"].to_numpy(dtype=int)
    )
    return bundle


def _log_metrics(prefix: str, metrics: dict[str, float]) -> None:
    mlflow.log_metrics({f"{prefix}_{name}": value for name, value in metrics.items()})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _partition_summary(partition: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": len(partition),
        "fraud_rows": int(partition["is_fraud"].sum()),
        "start": partition["event_timestamp"].min().isoformat(),
        "end": partition["event_timestamp"].max().isoformat(),
        "fraud_rate": float(partition["is_fraud"].mean()),
    }


def _encoded_to_raw_indices(encoded_names: np.ndarray) -> list[int]:
    """Map preprocessed columns back to the raw feature contract for explanations."""

    raw_indices: list[int] = []
    categorical_longest_first = sorted(
        CATEGORICAL_FEATURES, key=len, reverse=True
    )
    for encoded_name in encoded_names.astype(str):
        transformer, _, suffix = encoded_name.partition("__")
        if transformer == "numeric":
            raw_feature = suffix
        else:
            matches = [
                feature
                for feature in categorical_longest_first
                if suffix == feature or suffix.startswith(f"{feature}_")
            ]
            if not matches:
                raise ValueError(f"Cannot map encoded feature '{encoded_name}'.")
            raw_feature = matches[0]
        raw_indices.append(MODEL_FEATURES.index(raw_feature))
    return raw_indices


def train_and_package(
    input_path: Path,
    output_dir: Path,
    tracking_uri: str,
    experiment_name: str,
    target_fpr: float,
    top_k: int,
    seed: int,
    min_accuracy: float = 0.90,
    min_pr_auc: float = 0.65,
    min_recall_at_fpr: float = 0.65,
    review_target_recall: float = 0.90,
    registered_model_name: str = "aegis-risk-engine",
    bootstrap_iterations: int = 200,
) -> dict[str, Any]:
    """Run temporal model selection, final evaluation, and artifact packaging."""

    frame = pd.read_parquet(input_path)
    required_columns = MODEL_FEATURES + ["event_timestamp", "amount_usd", "is_fraud"]
    missing = set(required_columns).difference(frame.columns)
    if missing:
        raise ValueError(f"Training data is missing columns: {sorted(missing)}")
    partitions = temporal_split(frame)
    train_target = partitions["train"]["is_fraud"].to_numpy(dtype=int)
    positive_count = max(int(train_target.sum()), 1)
    class_weight = float((len(train_target) - positive_count) / positive_count)
    candidates = build_candidates(class_weight, seed)
    dataset_fingerprint = _file_sha256(input_path)
    schema_fingerprint = feature_schema_fingerprint()
    quality_gates = {
        "accuracy": min_accuracy,
        "pr_auc_average_precision": min_pr_auc,
        "recall_at_fixed_fpr": min_recall_at_fpr,
    }

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    assumptions = LossAssumptions()
    best_model: CalibratedRiskModel | None = None
    best_score = -np.inf
    validation_summaries: dict[str, dict[str, float]] = {}

    with mlflow.start_run(run_name="temporal-fraud-model-selection") as parent_run:
        mlflow.log_params(
            {
                "feature_version": FEATURE_VERSION,
                "selection_metric": "validation_pr_auc_average_precision",
                "target_fpr": target_fpr,
                "investigator_top_k": top_k,
                "positive_class_weight": class_weight,
                "random_seed": seed,
                "minimum_test_accuracy": min_accuracy,
                "minimum_test_pr_auc": min_pr_auc,
                "minimum_test_recall_at_fixed_fpr": min_recall_at_fpr,
                "review_target_recall": review_target_recall,
                "dataset_sha256": dataset_fingerprint,
                "feature_schema_sha256": schema_fingerprint,
            }
        )
        split_summary = {
            name: _partition_summary(partition)
            for name, partition in partitions.items()
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        split_path = output_dir / "temporal_splits.json"
        split_path.write_text(json.dumps(split_summary, indent=2), encoding="utf-8")
        mlflow.log_artifact(str(split_path), artifact_path="governance")

        for name, (pipeline, direction) in candidates.items():
            with mlflow.start_run(run_name=name, nested=True):
                LOGGER.info("Training %s", name)
                model = _fit_candidate(name, pipeline, direction, partitions)
                selection = partitions["selection"]
                probabilities = model.predict_proba(selection[MODEL_FEATURES])[:, 1]
                thresholds = derive_thresholds(
                    selection["is_fraud"].to_numpy(dtype=int),
                    probabilities,
                    target_fpr,
                    top_k,
                    review_target_recall,
                )
                model.thresholds = thresholds
                metrics, metadata = evaluate_predictions(
                    selection["is_fraud"].to_numpy(dtype=int),
                    probabilities,
                    selection["amount_usd"].to_numpy(dtype=float),
                    thresholds,
                    top_k,
                    assumptions,
                )
                validation_summaries[name] = metrics
                calibration_summary = calibration_comparison(
                    selection["is_fraud"].to_numpy(dtype=int),
                    model.predict_uncalibrated_proba(
                        selection[model.feature_names]
                    )[:, 1],
                    probabilities,
                )
                gate_failures = _quality_gate_failures(metrics, quality_gates)
                mlflow.log_params(
                    {
                        "model_name": name,
                        "review_threshold": thresholds.review,
                        "block_threshold": thresholds.block,
                        "validation_quality_gate_passed": not gate_failures,
                    }
                )
                _log_metrics("validation", metrics)
                for state, values in calibration_summary.items():
                    _log_metrics(f"validation_{state}", values)
                assumptions_path = output_dir / f"{name}_loss_assumptions.json"
                assumptions_path.write_text(
                    json.dumps(metadata, indent=2), encoding="utf-8"
                )
                mlflow.log_artifact(
                    str(assumptions_path), artifact_path="simulation"
                )
                mlflow.sklearn.log_model(model, artifact_path="calibrated_model")
                score = metrics["pr_auc_average_precision"]
                if not gate_failures and score > best_score:
                    best_score = score
                    best_model = model

        if best_model is None or best_model.thresholds is None:
            candidate_failures = {
                name: _quality_gate_failures(metrics, quality_gates)
                for name, metrics in validation_summaries.items()
            }
            raise RuntimeError(
                "No candidate passed the validation quality gates: "
                f"{json.dumps(candidate_failures, sort_keys=True)}"
            )

        model_version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        best_model.model_version = model_version
        test = partitions["test"]
        test_probabilities = best_model.predict_proba(test[MODEL_FEATURES])[:, 1]
        test_metrics, _ = evaluate_predictions(
            test["is_fraud"].to_numpy(dtype=int),
            test_probabilities,
            test["amount_usd"].to_numpy(dtype=float),
            best_model.thresholds,
            top_k,
            assumptions,
        )
        test_gate_failures = _quality_gate_failures(test_metrics, quality_gates)
        if test_gate_failures:
            mlflow.log_dict(test_gate_failures, "quality/test_gate_failures.json")
            raise RuntimeError(
                "Selected model failed untouched test quality gates: "
                f"{json.dumps(test_gate_failures, sort_keys=True)}"
            )
        mlflow.log_params(
            {
                "selected_model": best_model.model_name,
                "model_version": model_version,
                "review_threshold": best_model.thresholds.review,
                "block_threshold": best_model.thresholds.block,
            }
        )
        _log_metrics("test", test_metrics)

        calibration_summary = calibration_comparison(
            test["is_fraud"].to_numpy(dtype=int),
            best_model.predict_uncalibrated_proba(test[MODEL_FEATURES])[:, 1],
            test_probabilities,
        )
        bootstrap_summary = bootstrap_intervals(
            test["is_fraud"].to_numpy(dtype=int),
            test_probabilities,
            target_fpr=target_fpr,
            iterations=bootstrap_iterations,
            seed=seed,
        )
        segment_summary = segment_metrics(
            test,
            test_probabilities,
            target_fpr=target_fpr,
        )

        ablation_features = {
            "base_features_only": BASE_NUMERIC_FEATURES + CATEGORICAL_FEATURES,
            "base_plus_customer_velocity": BASE_NUMERIC_FEATURES
            + [name for name in VELOCITY_FEATURES if name.startswith("customer_")]
            + CATEGORICAL_FEATURES,
            "base_plus_customer_device_velocity": MODEL_FEATURES,
        }
        ablation_summary: dict[str, dict[str, float]] = {}
        for ablation_name, selected_features in ablation_features.items():
            if ablation_name == "base_plus_customer_device_velocity":
                ablation_model = best_model
            else:
                pipeline, direction = build_candidates(
                    class_weight,
                    seed,
                    feature_names=selected_features,
                )[best_model.model_name]
                ablation_model = _fit_candidate(
                    best_model.model_name,
                    pipeline,
                    direction,
                    partitions,
                    selected_features,
                )
            selection_probabilities = ablation_model.predict_proba(
                partitions["selection"][selected_features]
            )[:, 1]
            ablation_model.thresholds = derive_thresholds(
                partitions["selection"]["is_fraud"].to_numpy(dtype=int),
                selection_probabilities,
                target_fpr,
                top_k,
                review_target_recall,
            )
            ablation_probabilities = ablation_model.predict_proba(
                test[selected_features]
            )[:, 1]
            ablation_metrics, _ = evaluate_predictions(
                test["is_fraud"].to_numpy(dtype=int),
                ablation_probabilities,
                test["amount_usd"].to_numpy(dtype=float),
                ablation_model.thresholds,
                top_k,
                assumptions,
            )
            ablation_summary[ablation_name] = ablation_metrics
            _log_metrics(f"ablation_{ablation_name}", ablation_metrics)

        model_path = output_dir / "model.joblib"
        joblib.dump(best_model, model_path)
        background = partitions["train"][MODEL_FEATURES].sample(
            n=min(50, len(partitions["train"])), random_state=seed
        )
        preprocessor = best_model.estimator.named_steps["preprocess"]
        final_estimator = best_model.estimator.named_steps["model"]
        transformed_background = preprocessor.transform(background)
        if hasattr(transformed_background, "toarray"):
            transformed_background = transformed_background.toarray()
        if best_model.calibrator is None:
            raise RuntimeError("Selected model lost its fitted calibrator.")
        encoded_feature_names = preprocessor.get_feature_names_out()
        transformed_explainer = shap.Explainer(
            TransformedProbabilityFunction(
                final_estimator,
                best_model.calibrator,
                best_model.score_direction,
            ),
            np.asarray(transformed_background, dtype=float),
            algorithm="permutation",
            feature_names=encoded_feature_names.tolist(),
        )
        explainer = ShapExplainerArtifact(
            preprocessor=preprocessor,
            explainer=transformed_explainer,
            encoded_to_raw_index=_encoded_to_raw_indices(encoded_feature_names),
            raw_feature_names=MODEL_FEATURES,
        )
        explainer_path = output_dir / "shap_explainer.joblib"
        joblib.dump(explainer, explainer_path)
        background.to_parquet(output_dir / "shap_background.parquet", index=False)

        checksum = artifact_checksum(output_dir)
        input_example = test[MODEL_FEATURES].head(5)
        signature = infer_signature(
            input_example,
            best_model.predict_proba(input_example)[:, 1],
        )
        model_info = mlflow.sklearn.log_model(
            best_model,
            artifact_path="champion_candidate",
            signature=signature,
            input_example=input_example,
            registered_model_name=registered_model_name,
        )
        registered_version = getattr(model_info, "registered_model_version", None)
        if registered_version is None:
            raise RuntimeError("MLflow did not return a registered model version.")
        client = mlflow.MlflowClient()
        client.set_registered_model_alias(
            registered_model_name, "challenger", str(registered_version)
        )
        client.set_model_version_tag(
            registered_model_name,
            str(registered_version),
            "artifact_checksum",
            checksum,
        )
        client.set_model_version_tag(
            registered_model_name,
            str(registered_version),
            "feature_schema_fingerprint",
            schema_fingerprint,
        )

        manifest = {
            "model_name": best_model.model_name,
            "model_version": model_version,
            "feature_version": FEATURE_VERSION,
            "feature_schema": feature_schema_payload(),
            "feature_schema_fingerprint": schema_fingerprint,
            "dataset_fingerprint": {
                "sha256": dataset_fingerprint,
                "rows": len(frame),
                "event_start": frame["event_timestamp"].min().isoformat(),
                "event_end": frame["event_timestamp"].max().isoformat(),
            },
            "data_origin": "synthetic",
            "features": MODEL_FEATURES,
            "thresholds": best_model.thresholds.to_dict(),
            "selection_metric": "pr_auc_average_precision",
            "validation_metrics": validation_summaries[best_model.model_name],
            "test_metrics": test_metrics,
            "test_window": split_summary["test"],
            "calibration_comparison": calibration_summary,
            "bootstrap_95_intervals": bootstrap_summary,
            "feature_ablation": ablation_summary,
            "segment_metrics": segment_summary,
            "candidate_comparison": validation_summaries,
            "quality_gates": {
                "status": "passed",
                "minimums": quality_gates,
            },
            "loss_simulation": {
                "is_simulation": True,
                "assumptions": asdict(assumptions),
                "result": {
                    key: value
                    for key, value in test_metrics.items()
                    if "loss" in key or "monetary" in key
                },
            },
            "mlflow_run_id": parent_run.info.run_id,
            "mlflow_registered_model_name": registered_model_name,
            "mlflow_registered_model_version": str(registered_version),
            "mlflow_alias": "challenger",
            "artifact_checksum": checksum,
            "trained_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        mlflow.log_artifacts(str(output_dir), artifact_path="best_model")
        print(json.dumps(manifest, indent=2))
        return manifest


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/features.parquet"))
    parser.add_argument("--output-dir", type=Path, default=settings.model_dir)
    parser.add_argument("--tracking-uri", default=settings.mlflow_tracking_uri)
    parser.add_argument("--experiment-name", default=settings.mlflow_experiment_name)
    parser.add_argument("--target-fpr", type=float, default=settings.target_fpr)
    parser.add_argument("--top-k", type=int, default=settings.investigator_top_k)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-accuracy", type=float, default=0.90)
    parser.add_argument("--min-pr-auc", type=float, default=0.65)
    parser.add_argument("--min-recall-at-fpr", type=float, default=0.65)
    parser.add_argument("--review-target-recall", type=float, default=0.90)
    parser.add_argument(
        "--registered-model-name",
        default=settings.mlflow_registered_model_name,
    )
    parser.add_argument("--bootstrap-iterations", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    """CLI entry point."""

    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    train_and_package(
        input_path=args.input,
        output_dir=args.output_dir,
        tracking_uri=args.tracking_uri,
        experiment_name=args.experiment_name,
        target_fpr=args.target_fpr,
        top_k=args.top_k,
        seed=args.seed,
        min_accuracy=args.min_accuracy,
        min_pr_auc=args.min_pr_auc,
        min_recall_at_fpr=args.min_recall_at_fpr,
        review_target_recall=args.review_target_recall,
        registered_model_name=args.registered_model_name,
        bootstrap_iterations=args.bootstrap_iterations,
    )


if __name__ == "__main__":
    main()
