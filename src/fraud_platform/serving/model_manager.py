"""Thread-safe active-release loading with compatibility verification."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any

import joblib

from fraud_platform.features.definitions import (
    FEATURE_VERSION,
    MODEL_FEATURES,
    feature_schema_fingerprint,
)
from fraud_platform.models.artifacts import CalibratedRiskModel
from fraud_platform.models.release import (
    ReleasePointer,
    ReleaseValidationError,
    resolve_release_directory,
    rollback_release,
)


@dataclass(frozen=True)
class LoadedRelease:
    """One immutable serving snapshot."""

    model: CalibratedRiskModel
    explainer: Any
    manifest: dict[str, Any]
    pointer: ReleasePointer


class ModelManager:
    """Load off-lock and atomically swap verified serving snapshots."""

    def __init__(self, release_root: Path) -> None:
        self.release_root = release_root
        self._lock = RLock()
        self._operation_lock = Lock()
        self._active: LoadedRelease | None = None

    def snapshot(self) -> LoadedRelease | None:
        with self._lock:
            return self._active

    def _load(self, pointer_name: str = "current") -> LoadedRelease:
        directory, pointer = resolve_release_directory(
            self.release_root, pointer_name
        )
        manifest = json.loads(
            (directory / "manifest.json").read_text(encoding="utf-8")
        )
        model = joblib.load(directory / "model.joblib")
        explainer = joblib.load(directory / "shap_explainer.joblib")
        if not isinstance(model, CalibratedRiskModel):
            raise ReleaseValidationError("Artifact is not a CalibratedRiskModel")
        if model.feature_names != MODEL_FEATURES:
            raise ReleaseValidationError("Model feature order does not match serving")
        if model.feature_version != FEATURE_VERSION:
            raise ReleaseValidationError("Model feature version does not match serving")
        if model.feature_schema_fingerprint != feature_schema_fingerprint():
            raise ReleaseValidationError("Model schema fingerprint does not match serving")
        return LoadedRelease(model, explainer, manifest, pointer)

    def reload(self, warm: Callable[[CalibratedRiskModel, Any], None]) -> LoadedRelease:
        with self._operation_lock:
            candidate = self._load("current")
            warm(candidate.model, candidate.explainer)
            with self._lock:
                self._active = candidate
            return candidate

    def rollback(self, warm: Callable[[CalibratedRiskModel, Any], None]) -> LoadedRelease:
        with self._operation_lock:
            previous = self._load("previous")
            warm(previous.model, previous.explainer)
            rollback_release(self.release_root)
            with self._lock:
                self._active = previous
            return previous
