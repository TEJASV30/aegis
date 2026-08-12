"""Promote a gated MLflow challenger into the atomic local serving store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import mlflow
from mlflow import MlflowClient
from psycopg.types.json import Jsonb

from fraud_platform.config import get_settings
from fraud_platform.db import database_connection
from fraud_platform.models.release import (
    ReleasePointer,
    load_manifest,
    promote_candidate,
    read_pointer,
    resolve_release_directory,
)


def _verify_mlflow_challenger(
    manifest: dict[str, Any], tracking_uri: str
) -> tuple[MlflowClient, str, str]:
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    name = str(manifest["mlflow_registered_model_name"])
    version = str(manifest["mlflow_registered_model_version"])
    challenger = client.get_model_version_by_alias(name, "challenger")
    if str(challenger.version) != version:
        raise RuntimeError(
            f"MLflow challenger points to {challenger.version}, expected {version}."
        )
    registered = client.get_model_version(name, version)
    if registered.tags.get("artifact_checksum") != manifest["artifact_checksum"]:
        raise RuntimeError("MLflow challenger checksum does not match the candidate.")
    if (
        registered.tags.get("feature_schema_fingerprint")
        != manifest["feature_schema_fingerprint"]
    ):
        raise RuntimeError("MLflow challenger feature schema does not match.")
    return client, name, version


def _persist_release(
    manifest: dict[str, Any], pointer: ReleasePointer, previous: str | None
) -> None:
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO feature_contracts (
                feature_version, schema_fingerprint, contract
            ) VALUES (%s, %s, %s)
            ON CONFLICT (feature_version) DO NOTHING
            """,
            (
                manifest["feature_version"],
                manifest["feature_schema_fingerprint"],
                Jsonb(manifest["feature_schema"]),
            ),
        )
        registered_contract = connection.execute(
            """
            SELECT schema_fingerprint
            FROM feature_contracts
            WHERE feature_version = %s
            """,
            (manifest["feature_version"],),
        ).fetchone()
        if (
            registered_contract is None
            or registered_contract["schema_fingerprint"]
            != manifest["feature_schema_fingerprint"]
        ):
            raise RuntimeError(
                "Feature version is already registered with a different schema."
            )
        connection.execute(
            """
            UPDATE investigations AS investigation
            SET status = 'DEFERRED_RELEASE',
                disposition = 'RELEASE_SUPERSEDED',
                updated_at = NOW()
            FROM production_predictions AS prediction
            WHERE prediction.prediction_id = investigation.prediction_id
              AND investigation.status IN ('OPEN', 'ESCALATED')
              AND prediction.model_version <> %s
            """,
            (pointer.model_version,),
        )
        connection.execute(
            """
            UPDATE production_predictions
            SET queue_admitted = FALSE,
                policy_reason = policy_reason || ':release_superseded'
            WHERE queue_admitted AND model_version <> %s
            """,
            (pointer.model_version,),
        )
        connection.execute(
            """
            UPDATE model_releases SET status = 'ARCHIVED'
            WHERE status = 'CHAMPION' AND model_version <> %s
            """,
            (pointer.model_version,),
        )
        if previous:
            connection.execute(
                """
                UPDATE model_releases SET status = 'PREVIOUS'
                WHERE model_version = %s
                """,
                (previous,),
            )
        connection.execute(
            """
            INSERT INTO model_releases (
                model_version, feature_version, artifact_checksum,
                mlflow_run_id, mlflow_model_version, status, manifest, activated_at
            ) VALUES (%s, %s, %s, %s, %s, 'CHAMPION', %s, NOW())
            ON CONFLICT (model_version) DO UPDATE SET
                status = 'CHAMPION', manifest = EXCLUDED.manifest,
                activated_at = NOW()
            """,
            (
                pointer.model_version,
                manifest["feature_version"],
                pointer.artifact_checksum,
                manifest.get("mlflow_run_id"),
                manifest.get("mlflow_registered_model_version"),
                Jsonb(manifest),
            ),
        )
        connection.execute(
            """
            UPDATE model_release_state
            SET active_model_version = %s,
                previous_model_version = %s,
                updated_at = NOW()
            WHERE singleton
            """,
            (pointer.model_version, previous),
        )


def synchronize_rollback(release_root: Path, tracking_uri: str) -> ReleasePointer:
    """Synchronize registry aliases and PostgreSQL after a verified pointer swap."""

    directory, current = resolve_release_directory(release_root, "current")
    manifest = load_manifest(directory)
    previous = read_pointer(release_root, "previous")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient()
    name = str(manifest["mlflow_registered_model_name"])
    version = str(manifest["mlflow_registered_model_version"])
    client.set_registered_model_alias(name, "champion", version)
    client.set_model_version_tag(name, version, "promotion_status", "champion_rollback")
    if previous is not None:
        previous_directory, _ = resolve_release_directory(release_root, "previous")
        previous_manifest = load_manifest(previous_directory)
        previous_version = str(previous_manifest["mlflow_registered_model_version"])
        client.set_registered_model_alias(name, "previous-champion", previous_version)
        client.set_model_version_tag(
            name, previous_version, "promotion_status", "previous_champion"
        )
    _persist_release(
        manifest,
        current,
        previous.model_version if previous is not None else None,
    )
    return current


def promote(
    candidate_dir: Path,
    release_root: Path,
    tracking_uri: str,
) -> ReleasePointer:
    """Verify gates, move registry aliases, publish bytes, and record activation."""

    manifest = load_manifest(candidate_dir)
    client, name, version = _verify_mlflow_challenger(manifest, tracking_uri)
    current_pointer = read_pointer(release_root, "current")
    try:
        current = client.get_model_version_by_alias(name, "champion")
    except Exception:
        current = None
    if current is not None and str(current.version) != version:
        client.set_registered_model_alias(name, "previous-champion", str(current.version))
    client.set_registered_model_alias(name, "champion", version)
    pointer = promote_candidate(candidate_dir, release_root)
    previous_model_version = (
        current_pointer.model_version
        if current_pointer and current_pointer.model_version != pointer.model_version
        else None
    )
    client.set_model_version_tag(name, version, "aegis_model_version", pointer.model_version)
    client.set_model_version_tag(name, version, "promotion_status", "champion")
    _persist_release(manifest, pointer, previous_model_version)
    return pointer


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, default=settings.candidate_model_dir)
    parser.add_argument("--release-root", type=Path, default=settings.model_dir)
    parser.add_argument("--tracking-uri", default=settings.mlflow_tracking_uri)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pointer = promote(args.candidate_dir, args.release_root, args.tracking_uri)
    print(json.dumps(pointer.to_dict(), indent=2))


if __name__ == "__main__":
    main()
