"""Persist idempotent production decisions and capacity-bounded review work."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from fraud_platform.db import database_connection
from fraud_platform.features.currency import amount_to_usd
from fraud_platform.serving.schemas import TransactionRequest


class IdempotencyConflict(RuntimeError):
    """Raised when a transaction ID is reused with a different payload."""


@dataclass(frozen=True)
class PersistedDecision:
    """Final policy decision and its auditable response snapshot."""

    response: dict[str, Any]
    idempotent_replay: bool


def transaction_fingerprint(transaction: TransactionRequest) -> str:
    """Hash the normalized request contract for duplicate protection."""

    payload = transaction.model_dump(mode="json")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def fetch_idempotent_response(
    transaction_id: uuid.UUID, request_fingerprint: str
) -> dict[str, Any] | None:
    """Return a previous response or reject conflicting transaction reuse."""

    query = """
        SELECT request_fingerprint, response_snapshot
        FROM production_predictions
        WHERE transaction_id = %s
        ORDER BY scored_at DESC
        LIMIT 1
    """
    with database_connection() as connection:
        row = connection.execute(query, (transaction_id,)).fetchone()
    if row is None:
        return None
    existing = row["request_fingerprint"]
    if existing is None:
        raise IdempotencyConflict(
            "Transaction ID was scored by a legacy release and cannot be reused."
        )
    if existing != request_fingerprint:
        raise IdempotencyConflict(
            "Transaction ID is already associated with a different request payload."
        )
    response = dict(row["response_snapshot"])
    response["idempotent_replay"] = True
    return response


def _capacity_decision(
    connection: Any,
    model_decision: str,
    probability: float,
    review_capacity: int,
) -> tuple[str, str, bool, int | None]:
    """Apply a globally serialized, highest-risk open-review capacity policy."""

    if model_decision == "Approve":
        return "Approve", "below_review_threshold", False, None
    if model_decision == "Block":
        return "Block", "at_or_above_block_threshold", False, None

    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext('aegis-review-capacity'))"
    )
    active = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM investigations
        WHERE status IN ('OPEN', 'ESCALATED')
        """
    ).fetchone()
    count = int(active["count"] if active else 0)
    if count < review_capacity:
        return "Manually Review", "review_capacity_available", True, count + 1

    lowest = connection.execute(
        """
        SELECT investigation.prediction_id, prediction.fraud_probability
        FROM investigations AS investigation
        JOIN production_predictions AS prediction
          ON prediction.prediction_id = investigation.prediction_id
        WHERE investigation.status IN ('OPEN', 'ESCALATED')
          AND prediction.queue_admitted
        ORDER BY prediction.fraud_probability ASC, prediction.scored_at DESC
        LIMIT 1
        FOR UPDATE OF investigation, prediction
        """
    ).fetchone()
    if lowest is not None and probability > float(lowest["fraud_probability"]):
        connection.execute(
            """
            UPDATE investigations
            SET status = 'DEFERRED_CAPACITY',
                disposition = 'CAPACITY_DEFERRED',
                updated_at = NOW()
            WHERE prediction_id = %s
            """,
            (lowest["prediction_id"],),
        )
        connection.execute(
            """
            UPDATE production_predictions
            SET queue_admitted = FALSE,
                policy_reason = policy_reason || ':displaced_by_higher_risk'
            WHERE prediction_id = %s
            """,
            (lowest["prediction_id"],),
        )
        return (
            "Manually Review",
            "review_replaced_lower_risk_case",
            True,
            review_capacity,
        )
    return "Approve", "review_capacity_exhausted_below_cutline", False, None


def persist_prediction(
    *,
    prediction_id: uuid.UUID,
    transaction: TransactionRequest,
    request_fingerprint: str,
    model_name: str,
    model_version: str,
    feature_version: str,
    artifact_checksum: str,
    probability: float,
    model_decision: str,
    review_threshold: float,
    block_threshold: float,
    review_capacity: int,
    features: dict[str, Any],
    explanation: list[dict[str, Any]],
    explanation_base_value: float,
    explanation_remainder: float,
    correlation_id: str,
    feature_latency_ms: float,
    model_latency_ms: float,
    inference_latency_ms: float,
) -> PersistedDecision:
    """Atomically deduplicate, apply capacity, and persist the complete decision."""

    raw = transaction.model_dump()
    raw["amount_usd"] = amount_to_usd(transaction.amount, transaction.currency)
    with database_connection() as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(transaction.transaction_id),),
        )
        existing = connection.execute(
            """
            SELECT request_fingerprint, response_snapshot
            FROM production_predictions
            WHERE transaction_id = %s
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            (transaction.transaction_id,),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                raise IdempotencyConflict(
                    "Transaction ID is already associated with a different payload."
                )
            response = dict(existing["response_snapshot"])
            response["idempotent_replay"] = True
            return PersistedDecision(response, True)

        existing_raw = connection.execute(
            """
            SELECT transaction_id, event_timestamp, customer_id, merchant_id,
                   device_id, amount, currency, merchant_category, channel,
                   customer_age, account_age_days, distance_from_home_km,
                   is_foreign, device_age_days, failed_attempts_24h
            FROM raw_transactions
            WHERE transaction_id = %s
            FOR UPDATE
            """,
            (transaction.transaction_id,),
        ).fetchone()
        if existing_raw is not None:
            historical = TransactionRequest(**dict(existing_raw))
            if transaction_fingerprint(historical) != request_fingerprint:
                raise IdempotencyConflict(
                    "Transaction ID exists in history with different attributes."
                )

        connection.execute(
            """
            INSERT INTO raw_transactions (
                transaction_id, event_timestamp, customer_id, merchant_id, device_id,
                amount, amount_usd, currency, merchant_category, channel, customer_age,
                account_age_days, distance_from_home_km, is_foreign, device_age_days,
                failed_attempts_24h, is_fraud, fraud_type
            ) VALUES (
                %(transaction_id)s, %(event_timestamp)s, %(customer_id)s,
                %(merchant_id)s, %(device_id)s, %(amount)s, %(amount_usd)s,
                %(currency)s, %(merchant_category)s, %(channel)s, %(customer_age)s,
                %(account_age_days)s, %(distance_from_home_km)s, %(is_foreign)s,
                %(device_age_days)s, %(failed_attempts_24h)s, NULL, NULL
            ) ON CONFLICT (transaction_id) DO NOTHING
            """,
            raw,
        )
        decision, policy_reason, queue_admitted, queue_rank = _capacity_decision(
            connection, model_decision, probability, review_capacity
        )
        response = {
            "prediction_id": str(prediction_id),
            "transaction_id": str(transaction.transaction_id),
            "decision": decision,
            "model_decision": model_decision,
            "policy_reason": policy_reason,
            "queue_admitted": queue_admitted,
            "calibrated_probability": probability,
            "model_name": model_name,
            "model_version": model_version,
            "feature_version": feature_version,
            "artifact_checksum": artifact_checksum,
            "explanation": explanation,
            "explanation_base_value": explanation_base_value,
            "explanation_unit": "probability_delta",
            "explanation_remainder": explanation_remainder,
            "correlation_id": correlation_id,
            "idempotent_replay": False,
            "feature_latency_ms": feature_latency_ms,
            "model_latency_ms": model_latency_ms,
            "inference_latency_ms": inference_latency_ms,
        }
        connection.execute(
            """
            INSERT INTO production_predictions (
                prediction_id, transaction_id, event_timestamp, model_name,
                model_version, feature_version, artifact_checksum,
                fraud_probability, model_decision, decision, policy_reason,
                review_threshold, block_threshold, queue_admitted, features,
                explanation, explanation_base_value, explanation_unit,
                request_fingerprint, correlation_id, response_snapshot,
                feature_latency_ms, model_latency_ms, inference_latency_ms
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, 'probability_delta', %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                prediction_id,
                transaction.transaction_id,
                transaction.event_timestamp,
                model_name,
                model_version,
                feature_version,
                artifact_checksum,
                probability,
                model_decision,
                decision,
                policy_reason,
                review_threshold,
                block_threshold,
                queue_admitted,
                Jsonb(features),
                Jsonb(explanation),
                explanation_base_value,
                request_fingerprint,
                correlation_id,
                Jsonb(response),
                feature_latency_ms,
                model_latency_ms,
                inference_latency_ms,
            ),
        )
        if queue_admitted:
            connection.execute(
                """
                INSERT INTO investigations (
                    investigation_id, prediction_id, capacity_limit,
                    queue_rank_at_admission
                ) VALUES (%s, %s, %s, %s)
                """,
                (uuid.uuid4(), prediction_id, review_capacity, queue_rank),
            )
    return PersistedDecision(response, False)


def fetch_review_queue(
    limit: int = 100,
    *,
    model_version: str | None = None,
    review_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Return current-policy, capacity-admitted cases ranked by calibrated risk."""

    query = """
        SELECT p.prediction_id, p.transaction_id, p.scored_at,
               p.fraud_probability, p.decision, p.model_version,
               p.feature_version, p.policy_reason, p.review_threshold,
               p.block_threshold, p.features, p.explanation,
               p.explanation_base_value, p.explanation_unit,
               i.status, i.assignee
        FROM production_predictions AS p
        JOIN investigations AS i ON i.prediction_id = p.prediction_id
        WHERE p.decision = 'Manually Review'
          AND p.queue_admitted
          AND i.status IN ('OPEN', 'ESCALATED')
          AND (%s::TEXT IS NULL OR p.model_version = %s)
          AND (%s::DOUBLE PRECISION IS NULL OR p.fraud_probability >= %s)
        ORDER BY p.fraud_probability DESC, p.scored_at ASC
        LIMIT %s
    """
    with database_connection() as connection:
        rows = connection.execute(
            query,
            (
                model_version,
                model_version,
                review_threshold,
                review_threshold,
                limit,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_investigation(
    prediction_id: uuid.UUID,
    action: str,
    assignee: str,
    notes: str | None,
) -> dict[str, Any]:
    """Persist a human disposition and timestamp the supervised outcome."""

    resolution = {
        "APPROVE": ("RESOLVED", "LEGITIMATE", 0),
        "REJECT": ("RESOLVED", "FRAUD_CONFIRMED", 1),
        "ESCALATE": ("ESCALATED", "ESCALATED", None),
    }
    try:
        status, disposition, actual_is_fraud = resolution[action]
    except KeyError as error:
        raise ValueError(f"Unsupported investigation action: {action}") from error

    with database_connection() as connection:
        current = connection.execute(
            """
            SELECT i.status, p.transaction_id
            FROM investigations AS i
            JOIN production_predictions AS p ON p.prediction_id = i.prediction_id
            WHERE i.prediction_id = %s
            FOR UPDATE
            """,
            (prediction_id,),
        ).fetchone()
        if current is None:
            raise LookupError("Investigation not found")
        if current["status"] == "RESOLVED":
            raise RuntimeError("Investigation has already been resolved")

        connection.execute(
            """
            UPDATE investigations
            SET status = %s, disposition = %s, assignee = %s,
                notes = %s, updated_at = NOW()
            WHERE prediction_id = %s
              AND status IN ('OPEN', 'ESCALATED')
            """,
            (status, disposition, assignee, notes, prediction_id),
        )
        if actual_is_fraud is not None:
            connection.execute(
                """
                UPDATE production_predictions
                SET actual_is_fraud = %s, outcome_recorded_at = NOW()
                WHERE transaction_id = %s
                """,
                (actual_is_fraud, current["transaction_id"]),
            )
            connection.execute(
                """
                UPDATE raw_transactions
                SET is_fraud = %s,
                    fraud_type = CASE WHEN %s = 1
                        THEN COALESCE(fraud_type, 'investigator_confirmed')
                        ELSE NULL END
                WHERE transaction_id = %s
                """,
                (actual_is_fraud, actual_is_fraud, current["transaction_id"]),
            )
        row = connection.execute(
            """
            SELECT prediction_id, status, disposition, assignee, notes, updated_at
            FROM investigations WHERE prediction_id = %s
            """,
            (prediction_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Investigation update returned no row")
    return {
        **dict(row),
        "transaction_id": current["transaction_id"],
        "actual_is_fraud": actual_is_fraud,
    }


def fetch_data_summary() -> dict[str, Any]:
    """Return compact database counts for the platform console."""

    query = """
        SELECT
            (SELECT COUNT(*) FROM raw_transactions) AS raw_transactions,
            (SELECT COUNT(*) FROM raw_transactions WHERE is_fraud IS NOT NULL)
                AS labeled_transactions,
            (SELECT AVG(is_fraud::DOUBLE PRECISION) FROM raw_transactions
                WHERE is_fraud IS NOT NULL) AS observed_fraud_rate,
            (SELECT COUNT(*) FROM production_predictions)
                AS production_predictions,
            (SELECT COUNT(*) FROM investigations
                WHERE status IN ('OPEN', 'ESCALATED')) AS open_reviews,
            (SELECT MAX(event_timestamp) FROM raw_transactions)
                AS latest_event_timestamp
    """
    with database_connection() as connection:
        row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a data summary.")
    return dict(row)
