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
  const rawTransactions = randomInteger(80_000, 140_000);
  return {
    raw_transactions: rawTransactions,
    labeled_transactions: Math.floor(rawTransactions * 0.92),
    observed_fraud_rate: 0.040 + Math.random() * 0.008,
    production_predictions: randomInteger(25_000, 65_000),
    open_reviews: randomInteger(18, 80),
    latest_event_timestamp: new Date().toISOString(),
  };
}

export function simulateSyntheticBatch(
  rows: number,
  seed: number,
  days: number,
): SyntheticGenerationResponse {
  const fraudRate = 0.040 + Math.random() * 0.008;
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
