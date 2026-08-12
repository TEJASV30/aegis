from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from fraud_platform.db import database_connection


def test_feature_version_cannot_be_silently_redefined(
    require_postgres: None,
) -> None:
    feature_version = f"test-{uuid.uuid4()}"
    first_fingerprint = uuid.uuid4().hex
    second_fingerprint = uuid.uuid4().hex
    try:
        with database_connection() as connection:
            connection.execute(
                """
                INSERT INTO feature_contracts (
                    feature_version, schema_fingerprint, contract
                ) VALUES (%s, %s, %s)
                """,
                (feature_version, first_fingerprint, Jsonb({"features": ["a"]})),
            )
            connection.execute(
                """
                INSERT INTO feature_contracts (
                    feature_version, schema_fingerprint, contract
                ) VALUES (%s, %s, %s)
                ON CONFLICT (feature_version) DO NOTHING
                """,
                (feature_version, second_fingerprint, Jsonb({"features": ["b"]})),
            )
            stored = connection.execute(
                """
                SELECT schema_fingerprint, contract
                FROM feature_contracts
                WHERE feature_version = %s
                """,
                (feature_version,),
            ).fetchone()
        assert stored is not None
        assert stored["schema_fingerprint"] == first_fingerprint
        assert stored["contract"] == {"features": ["a"]}
    finally:
        with database_connection() as connection:
            connection.execute(
                "DELETE FROM feature_contracts WHERE feature_version = %s",
                (feature_version,),
            )
