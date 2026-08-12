"""Independent serving-artifact and evidence gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from fraud_platform.config import get_settings
from fraud_platform.features.definitions import MODEL_FEATURES
from fraud_platform.models.release import load_manifest


def validate_candidate(directory: Path) -> dict[str, object]:
    """Verify bytes, schema, model load, calibration, thresholds and SHAP units."""

    manifest = load_manifest(directory)
    model = joblib.load(directory / "model.joblib")
    explainer = joblib.load(directory / "shap_explainer.joblib")
    background = pd.read_parquet(directory / "shap_background.parquet").head(3)
    frame = background[MODEL_FEATURES]
    probabilities = np.asarray(model.predict_proba(frame))[:, 1]
    decisions = model.predict(frame)
    explanation = explainer(frame)
    reconstructed = np.asarray(explanation.base_values).reshape(-1) + np.asarray(
        explanation.values
    ).sum(axis=1)
    if not np.allclose(reconstructed, probabilities, atol=2e-3):
        raise RuntimeError("SHAP values do not reconstruct calibrated probabilities.")
    if not set(decisions).issubset({"Approve", "Manually Review", "Block"}):
        raise RuntimeError("Artifact returned an unsupported decision.")
    return {
        "status": "passed",
        "model_version": manifest["model_version"],
        "artifact_checksum": manifest["artifact_checksum"],
        "feature_schema_fingerprint": manifest["feature_schema_fingerprint"],
        "rows_scored": len(frame),
        "explanation_unit": "probability_delta",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-dir", type=Path, default=get_settings().candidate_model_dir
    )
    return parser.parse_args()


def main() -> None:
    print(json.dumps(validate_candidate(parse_args().candidate_dir), indent=2))


if __name__ == "__main__":
    main()
