export type Explanation = {
  feature: string;
  value: string | number | null;
  shap_value: number;
};

export type TransactionInput = {
  transaction_id: string;
  event_timestamp: string;
  customer_id: string;
  merchant_id: string;
  device_id: string;
  amount: number;
  currency: string;
  merchant_category: string;
  channel: string;
  customer_age: number;
  account_age_days: number;
  distance_from_home_km: number;
  is_foreign: number;
  device_age_days: number;
  failed_attempts_24h: number;
};

export type DecisionResponse = {
  prediction_id: string;
  transaction_id: string;
  decision: "Approve" | "Manually Review" | "Block";
  model_decision: "Approve" | "Manually Review" | "Block";
  policy_reason: string;
  queue_admitted: boolean;
  calibrated_probability: number;
  model_name: string;
  model_version: string;
  feature_version: string;
  artifact_checksum: string;
  explanation: Explanation[];
  explanation_base_value: number;
  explanation_unit: "probability_delta";
  explanation_remainder: number;
  correlation_id: string;
  idempotent_replay: boolean;
  feature_latency_ms: number;
  model_latency_ms: number;
  inference_latency_ms: number;
};

export type ReviewItem = {
  prediction_id: string;
  transaction_id: string;
  scored_at: string;
  fraud_probability: number;
  decision: string;
  model_version: string;
  feature_version: string;
  policy_reason: string;
  review_threshold: number;
  block_threshold: number;
  features: Record<string, string | number | null>;
  explanation: Explanation[];
  explanation_base_value: number;
  explanation_unit: "probability_delta";
  status: string;
  assignee: string | null;
};

export type InvestigationAction = "APPROVE" | "REJECT" | "ESCALATE";

export type InvestigationResolution = {
  prediction_id: string;
  transaction_id: string;
  status: "RESOLVED" | "ESCALATED";
  disposition: "LEGITIMATE" | "FRAUD_CONFIRMED" | "ESCALATED";
  assignee: string;
  notes: string | null;
  actual_is_fraud: number | null;
  updated_at: string;
};

export type DataSummary = {
  raw_transactions: number;
  labeled_transactions: number;
  observed_fraud_rate: number | null;
  production_predictions: number;
  open_reviews: number;
  latest_event_timestamp: string | null;
};

export type SyntheticGenerationResponse = {
  requested_rows: number;
  inserted_rows: number;
  fraud_rows: number;
  fraud_rate: number;
  event_start: string;
  event_end: string;
  seed: number;
};

export type PlatformService = {
  name: string;
  url: string;
  purpose: string;
};

export type ModelQuality = {
  gate_status: "passed";
  model_version: string;
  feature_version: string;
  test_window_start: string;
  test_window_end: string;
  test_sample_size: number;
  test_fraud_count: number;
  data_origin: "synthetic" | "external" | "production";
  measured_at: string;
  metric_definitions: Record<string, string>;
  accuracy: number;
  balanced_accuracy: number;
  pr_auc_average_precision: number;
  recall_at_fixed_fpr: number;
  precision_at_k: number;
  brier_score: number;
  expected_calibration_error: number;
  simulated_net_monetary_loss_avoided: number;
};

export type PlatformStatus = {
  status: "ready" | "degraded";
  model_loaded: boolean;
  model_name: string | null;
  model_version: string | null;
  feature_version: string | null;
  artifact_checksum: string | null;
  previous_model_version: string | null;
  model_quality: ModelQuality | null;
  synthetic_generation_enabled: boolean;
  services: PlatformService[];
};
