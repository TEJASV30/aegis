-- Idempotent upgrade for databases created before the Aegis v2 feature contract.
BEGIN;

ALTER TABLE raw_transactions ADD COLUMN IF NOT EXISTS amount_usd DOUBLE PRECISION;
UPDATE raw_transactions
SET amount_usd = amount * CASE UPPER(currency)
    WHEN 'USD' THEN 1.0 WHEN 'EUR' THEN 1.08 WHEN 'GBP' THEN 1.27
    WHEN 'INR' THEN 0.012 WHEN 'JPY' THEN 0.0067 WHEN 'SGD' THEN 0.74
    ELSE 1.0 END
WHERE amount_usd IS DISTINCT FROM amount * CASE UPPER(currency)
    WHEN 'USD' THEN 1.0 WHEN 'EUR' THEN 1.08 WHEN 'GBP' THEN 1.27
    WHEN 'INR' THEN 0.012 WHEN 'JPY' THEN 0.0067 WHEN 'SGD' THEN 0.74
    ELSE 1.0 END;
ALTER TABLE raw_transactions ALTER COLUMN amount_usd SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'raw_transactions'::regclass
          AND conname = 'raw_transactions_supported_currency_check'
    ) THEN
        ALTER TABLE raw_transactions ADD CONSTRAINT
            raw_transactions_supported_currency_check
            CHECK (UPPER(currency) IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'SGD'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS feature_contracts (
    feature_version TEXT PRIMARY KEY,
    schema_fingerprint TEXT NOT NULL UNIQUE,
    contract JSONB NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
DECLARE
    primary_columns TEXT[];
BEGIN
    SELECT ARRAY_AGG(attribute.attname ORDER BY key_column.ordinality)
    INTO primary_columns
    FROM pg_constraint AS constraint_row
    JOIN LATERAL UNNEST(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
      ON TRUE
    JOIN pg_attribute AS attribute
      ON attribute.attrelid = constraint_row.conrelid
     AND attribute.attnum = key_column.attnum
    WHERE constraint_row.conrelid = 'transaction_features'::regclass
      AND constraint_row.contype = 'p';

    IF primary_columns = ARRAY['transaction_id'] THEN
        ALTER TABLE transaction_features DROP CONSTRAINT transaction_features_pkey;
        ALTER TABLE transaction_features
            ADD PRIMARY KEY (transaction_id, feature_version);
    END IF;
END $$;

ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS feature_version TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS artifact_checksum TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS model_decision TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS policy_reason TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS review_threshold DOUBLE PRECISION;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS block_threshold DOUBLE PRECISION;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS queue_admitted BOOLEAN DEFAULT FALSE;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS explanation_base_value DOUBLE PRECISION;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS explanation_unit TEXT DEFAULT 'probability_delta';
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS correlation_id TEXT;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS response_snapshot JSONB;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS feature_latency_ms DOUBLE PRECISION;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS model_latency_ms DOUBLE PRECISION;
ALTER TABLE production_predictions ADD COLUMN IF NOT EXISTS outcome_recorded_at TIMESTAMPTZ;

UPDATE production_predictions
SET feature_version = COALESCE(feature_version, 'v1'),
    artifact_checksum = COALESCE(artifact_checksum, 'legacy-unverified'),
    model_decision = COALESCE(model_decision, decision),
    policy_reason = COALESCE(policy_reason, 'legacy_policy'),
    review_threshold = COALESCE(review_threshold, 0.0),
    block_threshold = COALESCE(block_threshold, 1.0),
    queue_admitted = COALESCE(queue_admitted, decision = 'Manually Review'),
    explanation_base_value = COALESCE(explanation_base_value, 0.0),
    correlation_id = COALESCE(correlation_id, prediction_id::TEXT),
    response_snapshot = COALESCE(response_snapshot, '{}'::JSONB),
    feature_latency_ms = COALESCE(feature_latency_ms, inference_latency_ms),
    model_latency_ms = COALESCE(model_latency_ms, 0.0);

ALTER TABLE investigations ADD COLUMN IF NOT EXISTS capacity_limit INTEGER;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS queue_rank_at_admission INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS uq_predictions_transaction_idempotent
    ON production_predictions (transaction_id)
    WHERE request_fingerprint IS NOT NULL;

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
INSERT INTO model_release_state (singleton) VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'model_releases'::regclass
          AND conname = 'model_releases_status_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%ARCHIVED%'
    ) THEN
        ALTER TABLE model_releases DROP CONSTRAINT model_releases_status_check;
        ALTER TABLE model_releases ADD CONSTRAINT model_releases_status_check
            CHECK (status IN (
                'CANDIDATE', 'CHALLENGER', 'CHAMPION', 'PREVIOUS',
                'ARCHIVED', 'REJECTED'
            ));
    END IF;
END $$;

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
    prediction.prediction_id,
    prediction.transaction_id,
    prediction.scored_at,
    prediction.fraud_probability,
    prediction.features,
    prediction.explanation,
    investigation.status,
    investigation.assignee,
    investigation.disposition,
    investigation.updated_at,
    prediction.model_version,
    prediction.feature_version,
    prediction.policy_reason,
    prediction.review_threshold,
    prediction.block_threshold,
    prediction.explanation_base_value,
    prediction.explanation_unit
FROM production_predictions AS prediction
JOIN investigations AS investigation
  ON investigation.prediction_id = prediction.prediction_id;

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

COMMIT;
