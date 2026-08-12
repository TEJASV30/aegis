import type {
  DataSummary,
  DecisionResponse,
  Explanation,
  ReviewItem,
  SyntheticGenerationResponse,
  TransactionInput,
} from "./types";

const categories = [
  "grocery",
  "fuel",
  "restaurant",
  "retail",
  "travel",
  "electronics",
  "digital_goods",
  "cash_withdrawal",
];
const channels = ["pos", "ecommerce", "mobile", "atm"];
const demoOperationsStorageKey = "aegis-demo-operations-v1";

export type DemoOperationsState = {
  schema_version: 1;
  summary: DataSummary;
  batch: SyntheticGenerationResponse | null;
  rows: number;
  days: number;
  seed: number;
  saved_at: string;
};

function pick<T>(values: T[]): T {
  return values[Math.floor(Math.random() * values.length)];
}

function randomInteger(minimum: number, maximum: number): number {
  return Math.floor(Math.random() * (maximum - minimum + 1)) + minimum;
}

export function generateSyntheticTransaction(riskier = false): TransactionInput {
  const customerNumber = randomInteger(1, 9_999_999);
  const deviceAge = riskier ? randomInteger(0, 3) : randomInteger(30, 1_800);
  return {
    transaction_id: crypto.randomUUID(),
    event_timestamp: new Date().toISOString(),
    customer_id: `cust_${customerNumber.toString().padStart(7, "0")}`,
    merchant_id: `merch_${randomInteger(1, 50_000).toString().padStart(6, "0")}`,
    device_id: `device_${crypto.randomUUID().slice(0, 12)}`,
    amount: Number(
      (riskier ? 1_200 + Math.random() * 4_800 : 5 + Math.random() * 450).toFixed(2),
    ),
    currency: pick(["USD", "EUR", "GBP", "INR", "JPY", "SGD"]),
    merchant_category: riskier
      ? pick(["digital_goods", "electronics", "travel"])
      : pick(categories),
    channel: riskier ? pick(["ecommerce", "mobile"]) : pick(channels),
    customer_age: randomInteger(18, 85),
    account_age_days: randomInteger(5, 5_000),
    distance_from_home_km: Number(
      (riskier ? 180 + Math.random() * 720 : Math.random() * 80).toFixed(1),
    ),
    is_foreign: riskier ? Number(Math.random() > 0.1) : Number(Math.random() > 0.97),
    device_age_days: deviceAge,
    failed_attempts_24h: riskier ? randomInteger(2, 7) : randomInteger(0, 1),
  };
}

function contribution(
  feature: string,
  value: string | number,
  shapValue: number,
): Explanation {
  return { feature, value, shap_value: shapValue };
}

export function simulateDecision(transaction: TransactionInput): DecisionResponse {
  const amountSignal = Math.min(transaction.amount / 2_500, 1.4) * 0.7;
  const distanceSignal = Math.min(transaction.distance_from_home_km / 500, 1.4) * 0.65;
  const foreignSignal = transaction.is_foreign * 0.8;
  const deviceSignal = transaction.device_age_days < 7 ? 0.85 : -0.2;
  const attemptsSignal = Math.min(transaction.failed_attempts_24h, 6) * 0.22;
  const channelSignal = transaction.channel === "ecommerce" ? 0.28 : -0.08;
  const accountSignal = transaction.account_age_days > 365 ? -0.22 : 0.18;
  const logit =
    -2.1 +
    amountSignal +
    distanceSignal +
    foreignSignal +
    deviceSignal +
    attemptsSignal +
    channelSignal +
    accountSignal;
  const probability = 1 / (1 + Math.exp(-logit));
  const decision =
    probability >= 0.78
      ? "Block"
      : probability >= 0.34
        ? "Manually Review"
        : "Approve";
  const explanation = [
    contribution("amount", transaction.amount, amountSignal * 0.18),
    contribution(
      "distance_from_home_km",
      transaction.distance_from_home_km,
      distanceSignal * 0.18,
    ),
    contribution("is_foreign", transaction.is_foreign, foreignSignal * 0.18),
    contribution("device_age_days", transaction.device_age_days, deviceSignal * 0.18),
    contribution(
      "failed_attempts_24h",
      transaction.failed_attempts_24h,
      attemptsSignal * 0.18,
    ),
    contribution("account_age_days", transaction.account_age_days, accountSignal * 0.18),
  ].sort((left, right) => Math.abs(right.shap_value) - Math.abs(left.shap_value));

  return {
    prediction_id: crypto.randomUUID(),
    transaction_id: transaction.transaction_id,
    decision,
    model_decision: decision,
    policy_reason:
      decision === "Manually Review"
        ? "admitted_within_review_capacity"
        : "model_threshold_policy",
    queue_admitted: decision === "Manually Review",
    calibrated_probability: probability,
    model_name: "browser-risk-simulation",
    model_version: "preview-only-not-trained",
    feature_version: "preview-v1",
    artifact_checksum: "browser-preview",
    explanation,
    explanation_base_value: 0.08,
    explanation_unit: "probability_delta",
    explanation_remainder: 0,
    correlation_id: crypto.randomUUID(),
    idempotent_replay: false,
    feature_latency_ms: Number((1 + Math.random() * 3).toFixed(2)),
    model_latency_ms: Number((1 + Math.random() * 2).toFixed(2)),
    inference_latency_ms: Number((2 + Math.random() * 8).toFixed(2)),
  };
}

export function toReviewItem(
  transaction: TransactionInput,
  decision: DecisionResponse,
): ReviewItem {
  return {
    prediction_id: decision.prediction_id,
    transaction_id: decision.transaction_id,
    scored_at: new Date().toISOString(),
    fraud_probability: decision.calibrated_probability,
    decision: decision.decision,
    model_version: decision.model_version,
    feature_version: decision.feature_version,
    policy_reason: decision.policy_reason,
    review_threshold: 0.34,
    block_threshold: 0.78,
    status: "OPEN",
    assignee: null,
    features: {
      amount: transaction.amount,
      channel: transaction.channel,
      merchant_category: transaction.merchant_category,
      is_foreign: transaction.is_foreign,
    },
    explanation: decision.explanation,
    explanation_base_value: decision.explanation_base_value,
    explanation_unit: "probability_delta",
  };
}

export function generateReviewItems(count: number): ReviewItem[] {
  const items: ReviewItem[] = [];
  let attempts = 0;
  while (items.length < count && attempts < count * 30) {
    attempts += 1;
    const transaction = generateSyntheticTransaction(true);
    const decision = simulateDecision(transaction);
    if (decision.decision === "Manually Review") {
      items.push(toReviewItem(transaction, decision));
    }
  }
  return items.sort((left, right) => right.fraud_probability - left.fraud_probability);
}

export function createSimulatedSummary(): DataSummary {
  return {
    raw_transactions: 100_000,
    labeled_transactions: 92_000,
    observed_fraud_rate: 0.044,
    production_predictions: 50_000,
    open_reviews: 42,
    latest_event_timestamp: "2026-08-12T06:33:51.442Z",
  };
}

function isFiniteNonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isValidSummary(value: unknown): value is DataSummary {
  if (typeof value !== "object" || value === null) return false;
  const summary = value as Partial<DataSummary>;
  return (
    isFiniteNonNegative(summary.raw_transactions) &&
    isFiniteNonNegative(summary.labeled_transactions) &&
    (summary.observed_fraud_rate === null ||
      (isFiniteNonNegative(summary.observed_fraud_rate) &&
        summary.observed_fraud_rate <= 1)) &&
    isFiniteNonNegative(summary.production_predictions) &&
    isFiniteNonNegative(summary.open_reviews) &&
    (summary.latest_event_timestamp === null ||
      typeof summary.latest_event_timestamp === "string")
  );
}

function isValidBatch(value: unknown): value is SyntheticGenerationResponse | null {
  if (value === null) return true;
  if (typeof value !== "object") return false;
  const batch = value as Partial<SyntheticGenerationResponse>;
  return (
    isFiniteNonNegative(batch.requested_rows) &&
    isFiniteNonNegative(batch.inserted_rows) &&
    isFiniteNonNegative(batch.fraud_rows) &&
    isFiniteNonNegative(batch.fraud_rate) &&
    batch.fraud_rate <= 1 &&
    typeof batch.event_start === "string" &&
    typeof batch.event_end === "string" &&
    isFiniteNonNegative(batch.seed)
  );
}

function createDefaultDemoOperationsState(): DemoOperationsState {
  return {
    schema_version: 1,
    summary: createSimulatedSummary(),
    batch: null,
    rows: 100_000,
    days: 120,
    seed: 42,
    saved_at: new Date().toISOString(),
  };
}

function isValidDemoOperationsState(value: unknown): value is DemoOperationsState {
  if (typeof value !== "object" || value === null) return false;
  const state = value as Partial<DemoOperationsState>;
  return (
    state.schema_version === 1 &&
    isValidSummary(state.summary) &&
    isValidBatch(state.batch) &&
    typeof state.rows === "number" &&
    Number.isInteger(state.rows) &&
    state.rows >= 100 &&
    state.rows <= 100_000 &&
    typeof state.days === "number" &&
    Number.isInteger(state.days) &&
    state.days >= 7 &&
    state.days <= 730 &&
    typeof state.seed === "number" &&
    Number.isInteger(state.seed) &&
    isFiniteNonNegative(state.seed) &&
    typeof state.saved_at === "string"
  );
}

export function saveDemoOperationsState(state: DemoOperationsState): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(demoOperationsStorageKey, JSON.stringify(state));
    return true;
  } catch {
    // Storage can be unavailable in strict privacy modes. The demo still works in memory.
    return false;
  }
}

export function loadDemoOperationsState(): DemoOperationsState {
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage.getItem(demoOperationsStorageKey);
      if (stored !== null) {
        const parsed: unknown = JSON.parse(stored);
        if (isValidDemoOperationsState(parsed)) return parsed;
      }
    } catch {
      // Replace malformed or inaccessible storage with a clean, versioned snapshot.
    }
  }

  const fallback = createDefaultDemoOperationsState();
  saveDemoOperationsState(fallback);
  return fallback;
}

export function resetDemoOperationsState(): DemoOperationsState {
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(demoOperationsStorageKey);
    } catch {
      // Continue with an in-memory reset if storage is unavailable.
    }
  }
  const reset = createDefaultDemoOperationsState();
  saveDemoOperationsState(reset);
  return reset;
}

export function simulateSyntheticBatch(
  rows: number,
  seed: number,
  days: number,
): SyntheticGenerationResponse {
  let state = (seed >>> 0) + 0x6d2b79f5;
  state = Math.imul(state ^ (state >>> 15), state | 1);
  state ^= state + Math.imul(state ^ (state >>> 7), state | 61);
  const seededFraction = ((state ^ (state >>> 14)) >>> 0) / 4_294_967_296;
  const fraudRate = 0.040 + seededFraction * 0.008;
  const end = new Date();
  const start = new Date(end.getTime() - days * 86_400_000);
  return {
    requested_rows: rows,
    inserted_rows: rows,
    fraud_rows: Math.round(rows * fraudRate),
    fraud_rate: fraudRate,
    event_start: start.toISOString(),
    event_end: end.toISOString(),
    seed,
  };
}
