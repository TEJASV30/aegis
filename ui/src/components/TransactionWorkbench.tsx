import { useState } from "react";
import type { CSSProperties } from "react";
import { demoMode, platformApi } from "../api";
import { formatRiskPercentage } from "../format";
import {
  generateSyntheticTransaction,
  simulateDecision,
  toReviewItem,
} from "../simulation";
import type { DecisionResponse, ReviewItem, TransactionInput } from "../types";
import { ShapExplanation } from "./ReviewQueue";
import { Icon } from "./Icons";

type TransactionWorkbenchProps = {
  modelLoaded: boolean;
  onReviewCreated: (item: ReviewItem) => void;
};

export function TransactionWorkbench({
  modelLoaded,
  onReviewCreated,
}: TransactionWorkbenchProps) {
  const [transaction, setTransaction] = useState<TransactionInput>(() =>
    generateSyntheticTransaction(),
  );
  const [result, setResult] = useState<DecisionResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof TransactionInput>(
    field: K,
    value: TransactionInput[K],
  ) {
    setTransaction((current) => ({ ...current, [field]: value }));
  }

  function replaceWithSynthetic(riskier = false) {
    setTransaction(generateSyntheticTransaction(riskier));
    setResult(null);
    setError(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const decision = demoMode
        ? simulateDecision(transaction)
        : await platformApi.score(transaction);
      setResult(decision);
      if (decision.decision === "Manually Review") {
        onReviewCreated(toReviewItem(transaction, decision));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not check this payment");
    } finally {
      setSubmitting(false);
    }
  }

  const disabledReason =
    !demoMode && !modelLoaded
      ? "Payment checking is not ready yet. Open System health to finish setup."
      : null;

  const resultTone = result?.decision.toLowerCase().replaceAll(" ", "-") ?? "";
  const riskStyle = result
    ? ({ "--risk-angle": `${result.calibrated_probability * 360}deg` } as CSSProperties)
    : undefined;

  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <p className="eyebrow"><span /> Payment check</p>
          <h1>Check a payment</h1>
          <p>Enter the payment details to see the recommended action and why.</p>
        </div>
        <div className="heading-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => replaceWithSynthetic(false)}
          >
            Try a typical payment
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => replaceWithSynthetic(true)}
          >
            Try a high-risk payment
          </button>
        </div>
      </div>
      {demoMode && (
        <div className="notice preview-notice">
          Hosted demo: decisions are calculated in your browser. The repository includes
          the complete Docker application for calibrated, history-aware decisions.
        </div>
      )}
      {disabledReason && <div className="notice warning">{disabledReason}</div>}
      {error && <div className="notice error">{error}</div>}

      <div className="score-layout">
        <form className="transaction-form" onSubmit={(event) => void submit(event)}>
          <div className="form-section-heading">
            <div>
              <p className="card-kicker">Payment details</p>
              <h2>Payment information</h2>
            </div>
            <span className="live-chip"><i /> 13 inputs</span>
          </div>
          <div className="form-grid">
            <div className="form-group-label">
              <span>01</span>
              <div><strong>Payment</strong><small>Amount, route and merchant</small></div>
            </div>
            <label>
              Amount
              <input
                aria-label="Amount"
                type="number"
                min="0"
                step="0.01"
                value={transaction.amount}
                onChange={(event) => update("amount", Number(event.target.value))}
              />
            </label>
            <label>
              Currency
              <select
                aria-label="Currency"
                value={transaction.currency}
                onChange={(event) => update("currency", event.target.value)}
              >
                {['USD', 'EUR', 'GBP', 'INR', 'JPY', 'SGD'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              Channel
              <select
                aria-label="Channel"
                value={transaction.channel}
                onChange={(event) => update("channel", event.target.value)}
              >
                {['pos', 'ecommerce', 'mobile', 'atm'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              Merchant category
              <select
                aria-label="Merchant category"
                value={transaction.merchant_category}
                onChange={(event) => update("merchant_category", event.target.value)}
              >
                {[
                  'grocery',
                  'fuel',
                  'restaurant',
                  'retail',
                  'travel',
                  'electronics',
                  'digital_goods',
                  'cash_withdrawal',
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <div className="form-group-label">
              <span>02</span>
              <div><strong>Customer and device</strong><small>Account history and device</small></div>
            </div>
            <label>
              Customer ID
              <input
                aria-label="Customer ID"
                value={transaction.customer_id}
                onChange={(event) => update("customer_id", event.target.value)}
              />
            </label>
            <label>
              Merchant ID
              <input
                aria-label="Merchant ID"
                value={transaction.merchant_id}
                onChange={(event) => update("merchant_id", event.target.value)}
              />
            </label>
            <label>
              Device ID
              <input
                aria-label="Device ID"
                value={transaction.device_id}
                onChange={(event) => update("device_id", event.target.value)}
              />
            </label>
            <label>
              Customer age
              <input
                aria-label="Customer age"
                type="number"
                min="18"
                max="120"
                value={transaction.customer_age}
                onChange={(event) => update("customer_age", Number(event.target.value))}
              />
            </label>
            <label>
              Account age (days)
              <input
                aria-label="Account age in days"
                type="number"
                min="0"
                value={transaction.account_age_days}
                onChange={(event) =>
                  update("account_age_days", Number(event.target.value))
                }
              />
            </label>
            <label>
              Device age (days)
              <input
                aria-label="Device age in days"
                type="number"
                min="0"
                value={transaction.device_age_days}
                onChange={(event) =>
                  update("device_age_days", Number(event.target.value))
                }
              />
            </label>
            <div className="form-group-label">
              <span>03</span>
              <div><strong>Location and access</strong><small>Distance, attempts and travel</small></div>
            </div>
            <label>
              Distance from home (km)
              <input
                aria-label="Distance from home"
                type="number"
                min="0"
                step="0.1"
                value={transaction.distance_from_home_km}
                onChange={(event) =>
                  update("distance_from_home_km", Number(event.target.value))
                }
              />
            </label>
            <label>
              Failed attempts (24h)
              <input
                aria-label="Failed attempts"
                type="number"
                min="0"
                value={transaction.failed_attempts_24h}
                onChange={(event) =>
                  update("failed_attempts_24h", Number(event.target.value))
                }
              />
            </label>
            <label>
              Foreign transaction
              <select
                aria-label="Foreign transaction"
                value={transaction.is_foreign}
                onChange={(event) => update("is_foreign", Number(event.target.value))}
              >
                <option value={0}>No</option>
                <option value={1}>Yes</option>
              </select>
            </label>
          </div>
          <button
            className="primary-button submit-button"
            type="submit"
            disabled={submitting || Boolean(disabledReason)}
          >
            <span>{submitting ? "Checking payment…" : "Check payment"}</span>
            {!submitting && <Icon name="arrow" />}
          </button>
        </form>

        <div className={`decision-panel ${result ? `has-result ${resultTone}` : ""}`}>
          {result ? (
            <>
              <div className="decision-topline">
                <p className="eyebrow"><span /> Recommendation</p>
                <span className="decision-sequence">AE-{result.prediction_id.slice(0, 6).toUpperCase()}</span>
              </div>
              <div className="risk-hero">
                <div className="risk-orbit" style={riskStyle}>
                  <div>
                    <strong>{formatRiskPercentage(result.calibrated_probability)}</strong>
                    <span>estimated fraud risk</span>
                  </div>
                </div>
                <div className="decision-copy">
                  <span className={`decision-badge ${resultTone}`}>{result.decision}</span>
                  <h2>{result.decision === "Approve" ? "This payment looks safe" : result.decision === "Block" ? "This payment looks unsafe" : "A person should review this payment"}</h2>
                  <p>{result.decision === "Approve" ? "The payment can continue under the current rules." : result.decision === "Block" ? "The risk is above the automatic block threshold." : "The details are mixed, so the payment has been added to the review queue."}</p>
                </div>
              </div>
              <div className="result-meta">
                <span><i className="meta-dot active" /> {result.policy_reason.replaceAll("_", " ")}</span>
                <span>Features {result.feature_version}</span>
                <span>{result.idempotent_replay ? "Previous result reused" : "New result"}</span>
                <span>{result.inference_latency_ms.toFixed(0)} ms</span>
              </div>
              <div className="policy-evidence compact">
                <span>Starting risk</span>
                <strong>{formatRiskPercentage(result.explanation_base_value)}</strong>
                <small>
                  Each item below shows how much it changed the estimated risk.
                </small>
              </div>
              <div className="evidence-heading">
                <span>Why this result</span>
                <small>Largest effect first</small>
              </div>
              <ShapExplanation values={result.explanation} />
            </>
          ) : (
            <div className="decision-placeholder">
              <div className="radar-placeholder">
                <span className="radar-ring ring-one" />
                <span className="radar-ring ring-two" />
                <span className="radar-sweep" />
                <Icon name="pulse" />
              </div>
              <p className="card-kicker">Ready to check</p>
              <h2>No result yet</h2>
              <p>Fill in the payment details, then select Check payment.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
