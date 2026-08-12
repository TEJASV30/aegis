CREATE TABLE IF NOT EXISTS raw_transactions (
    transaction_id UUID PRIMARY KEY,
    event_timestamp TIMESTAMPTZ NOT NULL,
    customer_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL CHECK (amount >= 0),
    amount_usd DOUBLE PRECISION NOT NULL CHECK (amount_usd >= 0),
    currency TEXT NOT NULL CHECK (
        UPPER(currency) IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'SGD')
    ),
    merchant_category TEXT NOT NULL,
    channel TEXT NOT NULL,
    customer_age INTEGER NOT NULL,
    account_age_days INTEGER NOT NULL,
    distance_from_home_km DOUBLE PRECISION NOT NULL,
    is_foreign INTEGER NOT NULL CHECK (is_foreign IN (0, 1)),
    device_age_days INTEGER NOT NULL,
    failed_attempts_24h INTEGER NOT NULL,
    is_fraud INTEGER CHECK (is_fraud IN (0, 1)),
    fraud_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_customer_time
    ON raw_transactions (customer_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_device_time
    ON raw_transactions (device_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_event_time
    ON raw_transactions (event_timestamp DESC);

CREATE TABLE IF NOT EXISTS transaction_features (
    transaction_id UUID NOT NULL REFERENCES raw_transactions(transaction_id),
    event_timestamp TIMESTAMPTZ NOT NULL,
    features JSONB NOT NULL,
    is_fraud INTEGER CHECK (is_fraud IN (0, 1)),
    feature_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (transaction_id, feature_version)
);

CREATE INDEX IF NOT EXISTS idx_features_time
    ON transaction_features (event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_features_gin
    ON transaction_features USING GIN (features);

CREATE TABLE IF NOT EXISTS feature_contracts (
    feature_version TEXT PRIMARY KEY,
    schema_fingerprint TEXT NOT NULL UNIQUE,
    contract JSONB NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS production_predictions (
    prediction_id UUID PRIMARY KEY,
    transaction_id UUID NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    artifact_checksum TEXT NOT NULL,
    fraud_probability DOUBLE PRECISION NOT NULL,
    model_decision TEXT NOT NULL CHECK (
        model_decision IN ('Approve', 'Manually Review', 'Block')
    ),
    decision TEXT NOT NULL CHECK (decision IN ('Approve', 'Manually Review', 'Block')),
    policy_reason TEXT NOT NULL,
    review_threshold DOUBLE PRECISION NOT NULL,
    block_threshold DOUBLE PRECISION NOT NULL,
    queue_admitted BOOLEAN NOT NULL DEFAULT FALSE,
    features JSONB NOT NULL,
    explanation JSONB NOT NULL,
    explanation_base_value DOUBLE PRECISION NOT NULL,
    explanation_unit TEXT NOT NULL DEFAULT 'probability_delta',
    request_fingerprint TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    response_snapshot JSONB NOT NULL,
    feature_latency_ms DOUBLE PRECISION NOT NULL,
    model_latency_ms DOUBLE PRECISION NOT NULL,
    inference_latency_ms DOUBLE PRECISION NOT NULL,
    actual_is_fraud INTEGER CHECK (actual_is_fraud IN (0, 1)),
    outcome_recorded_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_predictions_transaction_idempotent
    ON production_predictions (transaction_id)
    WHERE request_fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_predictions_scored_at
    ON production_predictions (scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_decision
    ON production_predictions (decision, scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_actual_outcome
    ON production_predictions (actual_is_fraud, scored_at DESC)
    WHERE actual_is_fraud IS NOT NULL;

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id UUID PRIMARY KEY,
    prediction_id UUID UNIQUE NOT NULL REFERENCES production_predictions(prediction_id),
    status TEXT NOT NULL DEFAULT 'OPEN',
    assignee TEXT,
    disposition TEXT,
    notes TEXT,
    capacity_limit INTEGER,
    queue_rank_at_admission INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_investigations_status
    ON investigations (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS model_releases (
    model_version TEXT PRIMARY KEY,
    feature_version TEXT NOT NULL,
    artifact_checksum TEXT NOT NULL UNIQUE,
    mlflow_run_id TEXT,
    mlflow_model_version TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'CANDIDATE', 'CHALLENGER', 'CHAMPION', 'PREVIOUS',
            'ARCHIVED', 'REJECTED'
        )
    ),
    manifest JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS model_release_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    active_model_version TEXT REFERENCES model_releases(model_version),
    previous_model_version TEXT REFERENCES model_releases(model_version),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO model_release_state (singleton)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS drift_reports (
    report_id UUID PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reference_start TIMESTAMPTZ NOT NULL,
    reference_end TIMESTAMPTZ NOT NULL,
    current_start TIMESTAMPTZ NOT NULL,
    current_end TIMESTAMPTZ NOT NULL,
    dataset_drift BOOLEAN NOT NULL,
    drifted_feature_count INTEGER NOT NULL,
    metrics JSONB NOT NULL,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS model_performance_snapshots (
    snapshot_id UUID PRIMARY KEY,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    cohort_start TIMESTAMPTZ NOT NULL,
    cohort_end TIMESTAMPTZ NOT NULL,
    maturity_cutoff TIMESTAMPTZ NOT NULL,
    sample_count INTEGER NOT NULL,
    fraud_count INTEGER NOT NULL,
    metrics JSONB NOT NULL,
    alert_status TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monitoring_alerts (
    alert_id UUID PRIMARY KEY,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    model_version TEXT,
    message TEXT NOT NULL,
    evidence JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS feature_distribution_snapshots (
    snapshot_id UUID NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    feature_name TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    missing_rate DOUBLE PRECISION NOT NULL,
    mean_value DOUBLE PRECISION,
    std_value DOUBLE PRECISION,
    p01_value DOUBLE PRECISION,
    p50_value DOUBLE PRECISION,
    p99_value DOUBLE PRECISION,
    category_frequencies JSONB,
    PRIMARY KEY (snapshot_id, feature_name)
);

CREATE OR REPLACE VIEW v_fraud_operations_hourly AS
SELECT
    DATE_TRUNC('hour', scored_at) AS hour,
    model_version,
    COUNT(*) AS transaction_count,
    AVG(fraud_probability) AS average_fraud_probability,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY inference_latency_ms)
        AS p95_inference_latency_ms,
    COUNT(*) FILTER (WHERE decision = 'Approve') AS approved_count,
    COUNT(*) FILTER (WHERE decision = 'Manually Review') AS reviewed_count,
    COUNT(*) FILTER (WHERE decision = 'Block') AS blocked_count,
    AVG(actual_is_fraud) FILTER (WHERE actual_is_fraud IS NOT NULL)
        AS observed_fraud_rate,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY feature_latency_ms)
        AS p95_feature_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY model_latency_ms)
        AS p95_model_latency_ms,
    COUNT(*) FILTER (WHERE queue_admitted) AS queue_admitted_count
FROM production_predictions
GROUP BY 1, 2;

CREATE OR REPLACE VIEW v_investigator_queue AS
SELECT
    p.prediction_id,
    p.transaction_id,
    p.scored_at,
    p.fraud_probability,
    p.features,
    p.explanation,
    i.status,
    i.assignee,
    i.disposition,
    i.updated_at,
    p.model_version,
    p.feature_version,
    p.policy_reason,
    p.review_threshold,
    p.block_threshold,
    p.explanation_base_value,
    p.explanation_unit
FROM production_predictions AS p
JOIN investigations AS i ON i.prediction_id = p.prediction_id;

CREATE OR REPLACE VIEW v_model_performance_latest AS
SELECT DISTINCT ON (model_version, feature_version)
    model_version,
    feature_version,
    cohort_start,
    cohort_end,
    maturity_cutoff,
    sample_count,
    fraud_count,
    metrics,
    alert_status,
    generated_at
FROM model_performance_snapshots
ORDER BY model_version, feature_version, generated_at DESC;
