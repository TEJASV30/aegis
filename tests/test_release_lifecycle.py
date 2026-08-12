from __future__ import annotations

import json
from pathlib import Path

from fraud_platform.features.definitions import (
    FEATURE_VERSION,
    MODEL_FEATURES,
    feature_schema_fingerprint,
    feature_schema_payload,
)
from fraud_platform.models.release import (
    artifact_checksum,
    promote_candidate,
    read_pointer,
    rollback_release,
)


def _candidate(path: Path, version: str, byte: bytes) -> None:
    path.mkdir(parents=True)
    (path / "model.joblib").write_bytes(byte + b"model")
    (path / "shap_explainer.joblib").write_bytes(byte + b"explainer")
    (path / "shap_background.parquet").write_bytes(byte + b"background")
    manifest = {
        "model_name": "decision_core",
        "model_version": version,
        "feature_version": FEATURE_VERSION,
        "feature_schema": feature_schema_payload(),
        "feature_schema_fingerprint": feature_schema_fingerprint(),
        "features": MODEL_FEATURES,
        "quality_gates": {"status": "passed"},
    }
    manifest["artifact_checksum"] = artifact_checksum(path)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_verified_promotion_and_rollback_preserve_immutable_releases(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    root = tmp_path / "releases"
    _candidate(first, "release-1", b"1")
    _candidate(second, "release-2", b"2")

    promote_candidate(first, root)
    promote_candidate(second, root)

    assert read_pointer(root, "current").model_version == "release-2"  # type: ignore[union-attr]
    assert read_pointer(root, "previous").model_version == "release-1"  # type: ignore[union-attr]
    restored = rollback_release(root)
    assert restored.model_version == "release-1"
    assert read_pointer(root, "previous").model_version == "release-2"  # type: ignore[union-attr]
