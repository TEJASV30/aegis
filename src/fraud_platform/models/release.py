"""Verified, atomic local release promotion and rollback."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fraud_platform.features.definitions import (
    FEATURE_VERSION,
    MODEL_FEATURES,
    feature_schema_fingerprint,
)

REQUIRED_ARTIFACTS = (
    "model.joblib",
    "shap_explainer.joblib",
    "shap_background.parquet",
    "manifest.json",
)
CHECKSUM_ARTIFACTS = (
    "model.joblib",
    "shap_explainer.joblib",
    "shap_background.parquet",
)


class ReleaseValidationError(RuntimeError):
    """Raised when an artifact cannot satisfy the active serving contract."""


@dataclass(frozen=True)
class ReleasePointer:
    """Immutable identity of one promoted release."""

    model_version: str
    artifact_checksum: str
    relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "model_version": self.model_version,
            "artifact_checksum": self.artifact_checksum,
            "relative_path": self.relative_path,
        }


def artifact_checksum(directory: Path) -> str:
    """Hash serving artifacts in a deterministic order."""

    digest = hashlib.sha256()
    for name in CHECKSUM_ARTIFACTS:
        path = directory / name
        if not path.is_file():
            raise ReleaseValidationError(f"Required checksum artifact is missing: {path}")
        digest.update(name.encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1_048_576), b""):
                digest.update(chunk)
    return digest.hexdigest()


def load_manifest(directory: Path) -> dict[str, Any]:
    """Load and verify a candidate or promoted manifest."""

    missing = [name for name in REQUIRED_ARTIFACTS if not (directory / name).is_file()]
    if missing:
        raise ReleaseValidationError(f"Release is missing artifacts: {missing}")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("feature_version") != FEATURE_VERSION:
        failures.append(
            f"feature version {manifest.get('feature_version')} != {FEATURE_VERSION}"
        )
    if manifest.get("features") != MODEL_FEATURES:
        failures.append("ordered model features do not match the serving contract")
    expected_schema = feature_schema_fingerprint()
    if manifest.get("feature_schema_fingerprint") != expected_schema:
        failures.append("feature schema fingerprint does not match serving code")
    measured_checksum = artifact_checksum(directory)
    if manifest.get("artifact_checksum") != measured_checksum:
        failures.append("artifact checksum does not match artifact bytes")
    if manifest.get("quality_gates", {}).get("status") != "passed":
        failures.append("quality gates are not marked passed")
    if failures:
        raise ReleaseValidationError("; ".join(failures))
    return manifest


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_pointer(release_root: Path, name: str = "current") -> ReleasePointer | None:
    """Read one release pointer if it exists."""

    path = release_root / f"{name}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ReleasePointer(**payload)


def resolve_release_directory(
    release_root: Path, pointer_name: str = "current"
) -> tuple[Path, ReleasePointer]:
    """Resolve and re-verify the directory referenced by a pointer."""

    pointer = read_pointer(release_root, pointer_name)
    if pointer is None:
        raise FileNotFoundError(f"No {pointer_name} release pointer in {release_root}")
    directory = (release_root / pointer.relative_path).resolve()
    root = release_root.resolve()
    if directory != root and root not in directory.parents:
        raise ReleaseValidationError("Release pointer escapes the configured root")
    manifest = load_manifest(directory)
    if manifest["model_version"] != pointer.model_version:
        raise ReleaseValidationError("Pointer model version does not match manifest")
    if manifest["artifact_checksum"] != pointer.artifact_checksum:
        raise ReleaseValidationError("Pointer checksum does not match manifest")
    return directory, pointer


def promote_candidate(candidate_dir: Path, release_root: Path) -> ReleasePointer:
    """Verify and atomically promote one candidate, preserving rollback state."""

    manifest = load_manifest(candidate_dir)
    version = str(manifest["model_version"])
    checksum = str(manifest["artifact_checksum"])
    releases = release_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    destination = releases / version
    if destination.exists():
        existing = load_manifest(destination)
        if existing["artifact_checksum"] != checksum:
            raise ReleaseValidationError(
                f"Release version {version} already exists with different bytes"
            )
    else:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{version}.", dir=str(releases))
        )
        try:
            for name in REQUIRED_ARTIFACTS:
                shutil.copy2(candidate_dir / name, temporary / name)
            load_manifest(temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    pointer = ReleasePointer(
        model_version=version,
        artifact_checksum=checksum,
        relative_path=str(destination.relative_to(release_root)),
    )
    current = read_pointer(release_root)
    if current and current != pointer:
        _atomic_json(release_root / "previous.json", current.to_dict())
    _atomic_json(release_root / "current.json", pointer.to_dict())
    return pointer


def rollback_release(release_root: Path) -> ReleasePointer:
    """Atomically swap the active and previous verified release pointers."""

    current = read_pointer(release_root, "current")
    previous = read_pointer(release_root, "previous")
    if current is None or previous is None:
        raise ReleaseValidationError("Both current and previous releases are required")
    resolve_release_directory(release_root, "previous")
    _atomic_json(release_root / "current.json", previous.to_dict())
    _atomic_json(release_root / "previous.json", current.to_dict())
    return previous
