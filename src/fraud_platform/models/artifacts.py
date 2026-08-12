"""Serializable calibrated-model objects shared with the serving process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from fraud_platform.models.decision import DecisionThresholds, make_decisions


def _probability_to_log_odds(probability: np.ndarray) -> np.ndarray:
    """Convert classifier probabilities to stable logits for Platt scaling."""

    clipped = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


@dataclass
class CalibratedRiskModel:
    """A raw estimator followed by temporal holdout Platt calibration."""

    model_name: str
    estimator: Any
    score_direction: float
    feature_names: list[str]
    thresholds: DecisionThresholds | None = None
    model_version: str = "unregistered"
    calibrator: LogisticRegression | None = None
    feature_version: str = "unversioned"
    feature_schema_fingerprint: str = "unverified"

    def _raw_score(self, frame: pd.DataFrame) -> np.ndarray:
        missing = sorted(set(self.feature_names).difference(frame.columns))
        if missing:
            raise ValueError(f"Scoring frame is missing model features: {missing}")
        selected = frame[self.feature_names]
        if hasattr(self.estimator, "decision_function"):
            score = np.asarray(self.estimator.decision_function(selected))
        elif hasattr(self.estimator, "predict_proba"):
            probability = np.asarray(self.estimator.predict_proba(selected))[:, 1]
            score = _probability_to_log_odds(probability)
        elif hasattr(self.estimator, "score_samples"):
            score = np.asarray(self.estimator.score_samples(selected))
        else:
            raise TypeError("Estimator exposes no supported continuous scoring method.")
        return self.score_direction * score.reshape(-1)

    def fit_calibrator(self, frame: pd.DataFrame, target: np.ndarray) -> None:
        """Fit Platt scaling on a strictly later calibration slice."""

        if np.unique(target).size != 2:
            raise ValueError("Calibration slice must contain both target classes.")
        scores = self._raw_score(frame).reshape(-1, 1)
        self.calibrator = LogisticRegression(C=1_000.0, max_iter=2_000)
        self.calibrator.fit(scores, target)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        """Return calibrated two-column class probabilities."""

        if self.calibrator is None:
            raise RuntimeError("The probability calibrator has not been fitted.")
        scores = self._raw_score(frame).reshape(-1, 1)
        return np.asarray(self.calibrator.predict_proba(scores))

    def predict_uncalibrated_proba(self, frame: pd.DataFrame) -> np.ndarray:
        """Return a comparable sigmoid transform of the directed raw score."""

        score = np.clip(self._raw_score(frame), -35.0, 35.0)
        positive = 1.0 / (1.0 + np.exp(-score))
        return np.column_stack((1.0 - positive, positive))

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """Return policy decisions rather than binary class labels."""

        if self.thresholds is None:
            raise RuntimeError("Decision thresholds have not been fitted.")
        return make_decisions(self.predict_proba(frame)[:, 1], self.thresholds)


@dataclass
class TransformedProbabilityFunction:
    """Pickle-safe calibrated scorer over preprocessed numeric matrices."""

    estimator: Any
    calibrator: LogisticRegression
    score_direction: float

    def __call__(self, values: Any) -> np.ndarray:
        if hasattr(self.estimator, "decision_function"):
            score = np.asarray(self.estimator.decision_function(values))
        elif hasattr(self.estimator, "predict_proba"):
            probability = np.asarray(self.estimator.predict_proba(values))[:, 1]
            score = _probability_to_log_odds(probability)
        elif hasattr(self.estimator, "score_samples"):
            score = np.asarray(self.estimator.score_samples(values))
        else:
            raise TypeError("Estimator exposes no supported continuous scoring method.")
        directed_score = self.score_direction * score.reshape(-1, 1)
        return np.asarray(self.calibrator.predict_proba(directed_score))[:, 1]


@dataclass
class AggregatedExplanation:
    """Minimal SHAP-compatible explanation returned to serving."""

    values: np.ndarray
    base_values: np.ndarray


@dataclass
class ShapExplainerArtifact:
    """Explain transformed predictions and aggregate one-hot values to raw fields."""

    preprocessor: Any
    explainer: Any
    encoded_to_raw_index: list[int]
    raw_feature_names: list[str]

    def __call__(self, frame: pd.DataFrame) -> AggregatedExplanation:
        transformed = self.preprocessor.transform(frame[self.raw_feature_names])
        if hasattr(transformed, "toarray"):
            transformed = transformed.toarray()
        explanation = self.explainer(np.asarray(transformed, dtype=float))
        encoded_values = np.asarray(explanation.values)
        if encoded_values.ndim == 3:
            encoded_values = encoded_values[:, :, -1]
        aggregated = np.zeros((len(frame), len(self.raw_feature_names)), dtype=float)
        for encoded_index, raw_index in enumerate(self.encoded_to_raw_index):
            aggregated[:, raw_index] += encoded_values[:, encoded_index]
        base_values = np.asarray(explanation.base_values, dtype=float)
        if base_values.ndim > 1:
            base_values = base_values[:, -1]
        return AggregatedExplanation(
            values=aggregated,
            base_values=base_values.reshape(-1),
        )
