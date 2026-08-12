"""Aegis calibrated real-time fraud decision API."""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mlflow.exceptions import MlflowException
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg import Error as PostgreSQLError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from fraud_platform.config import get_settings
from fraud_platform.db import ping_database
from fraud_platform.features.definitions import FEATURE_VERSION, MODEL_FEATURES
from fraud_platform.features.online import PostgreSQLFeatureStore
from fraud_platform.models.artifacts import CalibratedRiskModel
from fraud_platform.models.promote import synchronize_rollback
from fraud_platform.models.release import ReleaseValidationError, read_pointer
from fraud_platform.monitoring.logging import (
    IdempotencyConflict,
    fetch_data_summary,
    fetch_idempotent_response,
    fetch_review_queue,
    persist_prediction,
    resolve_investigation,
    transaction_fingerprint,
)
from fraud_platform.serving.model_manager import LoadedRelease, ModelManager
from fraud_platform.serving.schemas import (
    DataSummary,
    DecisionResponse,
    ErrorResponse,
    InvestigationResolutionRequest,
    InvestigationResolutionResponse,
    ModelQuality,
    ModelReloadResponse,
    ModelRollbackResponse,
    PlatformService,
    PlatformStatus,
    QueueItem,
    SyntheticGenerationRequest,
    SyntheticGenerationResponse,
    TransactionRequest,
)

LOGGER = logging.getLogger(__name__)
REQUEST_COUNT = Counter(
    "aegis_api_requests_total", "Aegis API requests", ["method", "path", "status"]
)
LATENCY = Histogram(
    "aegis_api_request_latency_seconds",
    "End-to-end HTTP request latency",
    ["method", "path"],
)
FEATURE_LATENCY = Histogram(
    "aegis_feature_query_latency_seconds", "Online feature-query latency"
)
MODEL_LATENCY = Histogram(
    "aegis_model_inference_latency_seconds", "Model and explanation latency"
)
DECISION_COUNT = Counter(
    "aegis_decisions_total",
    "Final operational decisions",
    ["decision", "model_version", "policy_reason"],
)

METRIC_DEFINITIONS = {
    "accuracy": "Binary accuracy at a fixed 0.5 probability cutoff on the untouched temporal test period.",
    "balanced_accuracy": "Mean of legitimate-class recall and fraud-class recall at a 0.5 cutoff.",
    "pr_auc_average_precision": "Average precision over the full precision-recall curve; headline rare-event ranking metric.",
    "recall_at_fixed_fpr": "Maximum fraud recall measured without exceeding the configured legitimate false-positive rate.",
    "precision_at_k": "Fraud prevalence among the highest-risk K test transactions, where K is review capacity.",
    "brier_score": "Mean squared error of calibrated probabilities; lower is better.",
    "expected_calibration_error": "Weighted confidence-versus-outcome gap across ten probability bins; lower is better.",
    "simulated_net_monetary_loss_avoided": "Scenario-based avoided loss versus approve-all under declared recovery and friction assumptions; not realized revenue.",
}


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Attach a stable request correlation ID to state, logs and responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied = request.headers.get("X-Correlation-ID", "").strip()
        correlation_id = supplied[:128] if supplied else str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require a configured API key outside the explicitly enabled demo mode."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()
        public_paths = {"/health", "/health/live", "/health/ready", "/metrics"}
        if (
            request.url.path.startswith("/v1/")
            and request.url.path not in public_paths
            and not settings.enable_demo_endpoints
        ):
            if not settings.api_key:
                return _error_response(
                    request,
                    503,
                    "AUTH_NOT_CONFIGURED",
                    "API authentication is required but AEGIS_API_KEY is not configured.",
                )
            if request.headers.get("X-Aegis-Api-Key") != settings.api_key:
                return _error_response(
                    request, 401, "UNAUTHORIZED", "A valid Aegis API key is required."
                )
        return await call_next(request)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Track request latency and periodically log rolling P95 and RPS."""

    def __init__(self, app: Any, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.window_seconds = window_seconds
        self.samples: deque[tuple[float, float]] = deque()
        self.lock = Lock()
        self.last_log_time = 0.0

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - started
            now = time.monotonic()
            REQUEST_COUNT.labels(request.method, request.url.path, str(status)).inc()
            LATENCY.labels(request.method, request.url.path).observe(elapsed)
            with self.lock:
                self.samples.append((now, elapsed))
                cutoff = now - self.window_seconds
                while self.samples and self.samples[0][0] < cutoff:
                    self.samples.popleft()
                if now - self.last_log_time >= 10.0 and self.samples:
                    durations = np.array([sample[1] for sample in self.samples])
                    observed_span = max(now - self.samples[0][0], 1.0)
                    LOGGER.info(
                        "serving_telemetry p95_latency_ms=%.3f rps=%.3f window=%d",
                        float(np.percentile(durations, 95) * 1_000.0),
                        len(self.samples)
                        / min(observed_span, float(self.window_seconds)),
                        self.window_seconds,
                    )
                    self.last_log_time = now


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    payload = ErrorResponse(
        error_code=code,
        message=message,
        correlation_id=correlation_id,
        details=details,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the feature store and atomically load a verified champion."""

    manager = ModelManager(get_settings().model_dir)
    app.state.model_manager = manager
    app.state.feature_store = PostgreSQLFeatureStore()
    try:
        await run_in_threadpool(manager.reload, _warm_artifacts)
    except (FileNotFoundError, ReleaseValidationError):
        LOGGER.warning(
            "No compatible promoted release is active; operational endpoints remain available.",
            exc_info=True,
        )
    yield


app = FastAPI(
    title="Aegis Transaction Intelligence API",
    version="1.0.0",
    lifespan=lifespan,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Aegis-Api-Key", "X-Correlation-ID"],
)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(
    TelemetryMiddleware, window_seconds=get_settings().telemetry_window_seconds
)
app.add_middleware(CorrelationMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return _error_response(
        request,
        422,
        "VALIDATION_ERROR",
        "Request validation failed.",
        list(error.errors()),
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
    detail = error.detail if isinstance(error.detail, str) else "Request failed."
    return _error_response(
        request,
        error.status_code,
        f"HTTP_{error.status_code}",
        detail,
        None if isinstance(error.detail, str) else error.detail,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _explain(
    explainer: Any,
    frame: pd.DataFrame,
    probability: float,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], float, float]:
    """Return top calibrated-probability SHAP deltas, base value and remainder."""

    result = explainer(frame)
    row_values = np.asarray(result.values, dtype=float)[0]
    base_value = float(np.asarray(result.base_values, dtype=float).reshape(-1)[0])
    reconstructed = base_value + float(row_values.sum())
    if not np.isclose(reconstructed, probability, atol=2e-3):
        raise RuntimeError(
            "Explanation does not reconstruct calibrated probability: "
            f"expected={probability:.6f}, reconstructed={reconstructed:.6f}"
        )
    ranked = np.argsort(np.abs(row_values))[::-1][:limit]
    values = [
        {
            "feature": MODEL_FEATURES[index],
            "value": _json_safe(frame.iloc[0, index]),
            "shap_value": float(row_values[index]),
        }
        for index in ranked
    ]
    displayed_sum = sum(item["shap_value"] for item in values)
    remainder = probability - base_value - displayed_sum
    return values, base_value, float(remainder)


def _warm_artifacts(model: CalibratedRiskModel, explainer: Any) -> None:
    """Pay one-time model and explanation initialization before atomic swap."""

    values: dict[str, Any] = {feature: 0.0 for feature in MODEL_FEATURES}
    values.update(
        {
            "amount_usd": 25.0,
            "customer_age": 35,
            "account_age_days": 365,
            "distance_from_home_km": 5.0,
            "device_age_days": 180,
            "event_hour": 12,
            "event_day_of_week": 2,
            "currency": "USD",
            "merchant_category": "grocery",
            "channel": "pos",
        }
    )
    frame = pd.DataFrame([values], columns=MODEL_FEATURES)
    started = time.perf_counter()
    probability = float(model.predict_proba(frame)[0, 1])
    model.predict(frame)
    _explain(explainer, frame, probability)
    LOGGER.info(
        "model_artifacts_warmed duration_ms=%.3f",
        (time.perf_counter() - started) * 1_000.0,
    )


def _active_release(request: Request) -> LoadedRelease | None:
    manager: ModelManager = request.app.state.model_manager
    return manager.snapshot()


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
@app.get("/health")
async def readiness(request: Request) -> JSONResponse:
    release = _active_release(request)
    try:
        database_ready = await run_in_threadpool(ping_database)
    except Exception:
        database_ready = False
    ready = release is not None and database_ready
    payload = {
        "status": "ready" if ready else "not_ready",
        "database_ready": database_ready,
        "model_loaded": release is not None,
        "model_version": release.model.model_version if release else None,
        "feature_version": FEATURE_VERSION,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/v1/platform", response_model=PlatformStatus)
async def platform_status(request: Request) -> PlatformStatus:
    """Expose active release evidence and open-source control planes."""

    settings = get_settings()
    release = _active_release(request)
    manifest = release.manifest if release else {}
    test_metrics = manifest.get("test_metrics", {})
    test_window = manifest.get("test_window", {})
    quality_fields = [
        "accuracy",
        "balanced_accuracy",
        "pr_auc_average_precision",
        "recall_at_fixed_fpr",
        "precision_at_k",
        "brier_score",
        "expected_calibration_error",
        "simulated_net_monetary_loss_avoided",
    ]
    quality = None
    if (
        manifest.get("quality_gates", {}).get("status") == "passed"
        and all(field in test_metrics for field in quality_fields)
        and all(
            field in test_window
            for field in ("start", "end", "rows", "fraud_rows")
        )
    ):
        quality = ModelQuality(
            gate_status="passed",
            model_version=manifest["model_version"],
            feature_version=manifest["feature_version"],
            test_window_start=test_window["start"],
            test_window_end=test_window["end"],
            test_sample_size=test_window["rows"],
            test_fraud_count=test_window["fraud_rows"],
            data_origin=manifest.get("data_origin", "synthetic"),
            measured_at=manifest["trained_at"],
            metric_definitions=METRIC_DEFINITIONS,
            **{field: test_metrics[field] for field in quality_fields},
        )
    previous = read_pointer(settings.model_dir, "previous")
    api_url = str(settings.public_api_url).rstrip("/")
    return PlatformStatus(
        status="ready" if release else "degraded",
        model_loaded=release is not None,
        model_name=release.model.model_name if release else None,
        model_version=release.model.model_version if release else None,
        feature_version=FEATURE_VERSION if release else None,
        artifact_checksum=release.pointer.artifact_checksum if release else None,
        previous_model_version=previous.model_version if previous else None,
        model_quality=quality,
        synthetic_generation_enabled=settings.enable_demo_endpoints,
        services=[
            PlatformService(
                name="Apache Airflow",
                url=str(settings.public_airflow_url),
                purpose="Run feature, training, release, and drift workflows",
            ),
            PlatformService(
                name="MLflow",
                url=str(settings.public_mlflow_url),
                purpose="Audit candidate runs, signatures, aliases, and releases",
            ),
            PlatformService(
                name="Apache Superset",
                url=str(settings.public_superset_url),
                purpose="Explore risk, latency, outcomes, and drift",
            ),
            PlatformService(
                name="FastAPI",
                url=f"{api_url}/docs",
                purpose="Inspect and exercise the decision contract",
            ),
        ],
    )


@app.get("/v1/data-summary", response_model=DataSummary)
async def data_summary() -> DataSummary:
    try:
        return DataSummary(**(await run_in_threadpool(fetch_data_summary)))
    except Exception as error:
        LOGGER.exception("Could not read the PostgreSQL data summary")
        raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from error


@app.post("/v1/synthetic-data", response_model=SyntheticGenerationResponse)
async def create_synthetic_data(
    generation: SyntheticGenerationRequest,
) -> SyntheticGenerationResponse:
    settings = get_settings()
    if not settings.enable_demo_endpoints:
        raise HTTPException(status_code=403, detail="Synthetic generation is disabled")
    from fraud_platform.data.generate_synthetic import (
        generate_transactions,
        write_transactions_to_postgres,
    )

    frame = await run_in_threadpool(
        generate_transactions,
        generation.rows,
        min(generation.customers, generation.rows),
        min(generation.merchants, generation.rows),
        generation.start.isoformat(),
        generation.days,
        generation.seed,
    )
    try:
        inserted_rows = await run_in_threadpool(write_transactions_to_postgres, frame)
    except Exception as error:
        LOGGER.exception("Could not write synthetic transactions")
        raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from error
    return SyntheticGenerationResponse(
        requested_rows=len(frame),
        inserted_rows=inserted_rows,
        fraud_rows=int(frame["is_fraud"].sum()),
        fraud_rate=float(frame["is_fraud"].mean()),
        event_start=frame["event_timestamp"].min().to_pydatetime(),
        event_end=frame["event_timestamp"].max().to_pydatetime(),
        seed=generation.seed,
    )


def _release_response(release: LoadedRelease, previous: str | None) -> dict[str, Any]:
    return {
        "model_name": release.model.model_name,
        "model_version": release.model.model_version,
        "feature_version": release.model.feature_version,
        "artifact_checksum": release.pointer.artifact_checksum,
        "previous_model_version": previous,
    }


@app.post("/v1/model/reload", response_model=ModelReloadResponse)
async def reload_model(request: Request) -> ModelReloadResponse:
    """Warm and atomically activate the already promoted champion pointer."""

    manager: ModelManager = request.app.state.model_manager
    try:
        release = await run_in_threadpool(manager.reload, _warm_artifacts)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="No promoted release exists; complete the Airflow promotion workflow.",
        ) from error
    except ReleaseValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    previous = read_pointer(get_settings().model_dir, "previous")
    return ModelReloadResponse(
        **_release_response(release, previous.model_version if previous else None)
    )


@app.post("/v1/model/rollback", response_model=ModelRollbackResponse)
async def rollback_model(request: Request) -> ModelRollbackResponse:
    """Warm the previous verified release and atomically restore it."""

    manager: ModelManager = request.app.state.model_manager
    try:
        release = await run_in_threadpool(manager.rollback, _warm_artifacts)
    except (FileNotFoundError, ReleaseValidationError) as error:
        LOGGER.exception("Verified model rollback could not be completed")
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        LOGGER.exception("Rollback pointer storage is unavailable")
        raise HTTPException(
            status_code=503,
            detail="Rollback storage is temporarily unavailable.",
        ) from error

    try:
        await run_in_threadpool(
            synchronize_rollback,
            get_settings().model_dir,
            get_settings().mlflow_tracking_uri,
        )
    except (MlflowException, OSError, PostgreSQLError, RuntimeError) as error:
        LOGGER.exception("Rollback synchronization failed; restoring prior release")
        try:
            await run_in_threadpool(manager.rollback, _warm_artifacts)
            await run_in_threadpool(
                synchronize_rollback,
                get_settings().model_dir,
                get_settings().mlflow_tracking_uri,
            )
        except Exception:
            LOGGER.critical(
                "Rollback compensation failed; operator reconciliation is required",
                exc_info=True,
            )
        raise HTTPException(
            status_code=503,
            detail="Rollback could not synchronize and was reverted.",
        ) from error
    previous = read_pointer(get_settings().model_dir, "previous")
    return ModelRollbackResponse(
        **_release_response(release, previous.model_version if previous else None)
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/decision", response_model=DecisionResponse)
async def score_transaction(
    transaction: TransactionRequest,
    request: Request,
) -> DecisionResponse:
    """Deduplicate, build prior-only features, score, explain and persist atomically."""

    started = time.perf_counter()
    correlation_id = request.state.correlation_id
    fingerprint = transaction_fingerprint(transaction)
    try:
        prior = await run_in_threadpool(
            fetch_idempotent_response, transaction.transaction_id, fingerprint
        )
    except IdempotencyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if prior is not None:
        return DecisionResponse(**prior)

    release = _active_release(request)
    if release is None:
        raise HTTPException(
            status_code=503,
            detail="No compatible champion is active; complete promotion and reload.",
        )
    model = release.model
    if model.thresholds is None:
        raise HTTPException(status_code=503, detail="Active policy has no thresholds")
    try:
        feature_started = time.perf_counter()
        feature_values = await run_in_threadpool(
            request.app.state.feature_store.calculate, transaction.model_dump()
        )
        feature_latency_ms = (time.perf_counter() - feature_started) * 1_000.0
        FEATURE_LATENCY.observe(feature_latency_ms / 1_000.0)
        frame = pd.DataFrame([feature_values], columns=MODEL_FEATURES)

        model_started = time.perf_counter()
        probability = float((await run_in_threadpool(model.predict_proba, frame))[0, 1])
        model_decision = str((await run_in_threadpool(model.predict, frame))[0])
        explanation, base_value, remainder = await run_in_threadpool(
            _explain, release.explainer, frame, probability
        )
        model_latency_ms = (time.perf_counter() - model_started) * 1_000.0
        MODEL_LATENCY.observe(model_latency_ms / 1_000.0)
    except Exception as error:
        LOGGER.exception("Transaction scoring failed correlation_id=%s", correlation_id)
        raise HTTPException(
            status_code=503, detail="Scoring temporarily unavailable"
        ) from error

    inference_latency_ms = (time.perf_counter() - started) * 1_000.0
    audit_features = {
        **feature_values,
        "amount": transaction.amount,
        "merchant_id": transaction.merchant_id,
    }
    try:
        persisted = await run_in_threadpool(
            persist_prediction,
            prediction_id=uuid.uuid4(),
            transaction=transaction,
            request_fingerprint=fingerprint,
            model_name=model.model_name,
            model_version=model.model_version,
            feature_version=FEATURE_VERSION,
            artifact_checksum=release.pointer.artifact_checksum,
            probability=probability,
            model_decision=model_decision,
            review_threshold=model.thresholds.review,
            block_threshold=model.thresholds.block,
            review_capacity=model.thresholds.review_capacity,
            features=audit_features,
            explanation=explanation,
            explanation_base_value=base_value,
            explanation_remainder=remainder,
            correlation_id=correlation_id,
            feature_latency_ms=feature_latency_ms,
            model_latency_ms=model_latency_ms,
            inference_latency_ms=inference_latency_ms,
        )
    except IdempotencyConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        LOGGER.exception("Decision persistence failed correlation_id=%s", correlation_id)
        raise HTTPException(
            status_code=503,
            detail="Decision could not be persisted; no partial decision was accepted.",
        ) from error
    response = DecisionResponse(**persisted.response)
    DECISION_COUNT.labels(
        response.decision, response.model_version, response.policy_reason
    ).inc()
    return response


@app.get("/v1/review-queue", response_model=list[QueueItem])
async def review_queue(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[QueueItem]:
    """Expose only current-policy cases admitted under the capacity limit."""

    release = _active_release(request)
    if release is None or release.model.thresholds is None:
        return []
    rows = await run_in_threadpool(
        fetch_review_queue,
        limit,
        model_version=release.model.model_version,
        review_threshold=release.model.thresholds.review,
    )
    return [QueueItem(**row) for row in rows]


@app.patch(
    "/v1/investigations/{prediction_id}",
    response_model=InvestigationResolutionResponse,
)
async def update_investigation(
    prediction_id: uuid.UUID,
    resolution: InvestigationResolutionRequest,
) -> InvestigationResolutionResponse:
    """Persist a human outcome with an explicit label-maturity timestamp."""

    try:
        result = await run_in_threadpool(
            resolve_investigation,
            prediction_id,
            resolution.action,
            resolution.assignee,
            resolution.notes,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        LOGGER.exception("Could not update investigation")
        raise HTTPException(
            status_code=503, detail="Investigation update unavailable"
        ) from error
    return InvestigationResolutionResponse(**result)
