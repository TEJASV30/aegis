import { useCallback, useEffect, useState } from "react";
import { platformApi } from "../api";
import { formatRiskPercentage } from "../format";
import type { Explanation, InvestigationAction, ReviewItem } from "../types";
import { Icon } from "./Icons";

export function ShapExplanation({ values }: { values: Explanation[] }) {
  const maximum = Math.max(...values.map((item) => Math.abs(item.shap_value)), 0.001);
  return (
    <div className="explanation" aria-label="Why this result">
      {values.map((item) => {
        const width = `${Math.max(4, (Math.abs(item.shap_value) / maximum) * 100)}%`;
        const direction = item.shap_value >= 0 ? "risk-up" : "risk-down";
        return (
          <div className="factor" key={item.feature}>
            <div className="factor-label">
              <span>{item.feature.replaceAll("_", " ")}</span>
              <span>{String(item.value ?? "missing")}</span>
            </div>
            <div className="bar-track">
              <div className={`bar ${direction}`} style={{ width }} />
            </div>
            <small>
              {item.shap_value >= 0 ? "raises" : "lowers"} calibrated risk by{" "}
              {(Math.abs(item.shap_value) * 100).toFixed(2)} percentage points
            </small>
          </div>
        );
      })}
    </div>
  );
}

type ReviewQueueProps = {
  previewMode: boolean;
  previewItems: ReviewItem[];
  onRegeneratePreview: () => void;
  onResolvePreview: (predictionId: string, action: InvestigationAction) => void;
};

export function ReviewQueue({
  previewMode,
  previewItems,
  onRegeneratePreview,
  onResolvePreview,
}: ReviewQueueProps) {
  const [items, setItems] = useState<ReviewItem[]>(previewItems);
  const [selected, setSelected] = useState<ReviewItem | null>(previewItems[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!previewMode);
  const [actionLoading, setActionLoading] = useState<InvestigationAction | null>(null);
  const [notes, setNotes] = useState("");
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (previewMode) {
      onRegeneratePreview();
      return;
    }
    setLoading(true);
    try {
      const payload = await platformApi.getReviewQueue();
      setItems(payload);
      setSelected((current) =>
        payload.find((item) => item.prediction_id === current?.prediction_id) ??
        payload[0] ??
        null,
      );
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Queue unavailable");
    } finally {
      setLoading(false);
    }
  }, [onRegeneratePreview, previewMode]);

  useEffect(() => {
    if (previewMode) {
      setItems(previewItems);
      setSelected((current) =>
        previewItems.find((item) => item.prediction_id === current?.prediction_id) ??
        previewItems[0] ??
        null,
      );
      setLoading(false);
      return;
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [previewItems, previewMode, refresh]);

  async function resolveSelected(action: InvestigationAction) {
    if (!selected) return;
    if (
      action === "REJECT" &&
      !window.confirm("Reject this transaction and label it as confirmed fraud?")
    ) {
      return;
    }

    setActionLoading(action);
    setError(null);
    setSuccess(null);
    const predictionId = selected.prediction_id;
    const transactionId = selected.transaction_id.slice(0, 13);
    try {
      if (previewMode) {
        onResolvePreview(predictionId, action);
      } else {
        await platformApi.resolveInvestigation(predictionId, action, notes);
        await refresh();
      }
      setNotes("");
      const message =
        action === "APPROVE"
          ? `Transaction ${transactionId} approved as legitimate.`
          : action === "REJECT"
            ? `Transaction ${transactionId} rejected as confirmed fraud.`
            : `Transaction ${transactionId} escalated for additional review.`;
      setSuccess(message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not resolve case");
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <p className="eyebrow"><span /> Manual review</p>
          <h1>Review cases</h1>
          <p>Review flagged payments, understand the reasons, and record an outcome.</p>
        </div>
        <div className="queue-heading-actions">
          <div className="heading-stat">
            <strong>{items.length}</strong>
            <span>open cases</span>
          </div>
          <button className="primary-button icon-button" onClick={() => void refresh()}>
            <Icon name="refresh" />
            {previewMode ? "New sample" : "Refresh"}
          </button>
        </div>
      </div>
      {previewMode && (
        <div className="notice preview-notice">
          Preview cases are generated in your browser. The connected workspace streams
          live decisions and investigation outcomes.
        </div>
      )}
      {error && <div className="notice error">{error}</div>}
      {success && <div className="notice success">{success}</div>}
      {loading ? (
        <div className="empty-card">Loading review cases…</div>
      ) : (
        <div className="workspace">
          <div className="queue" role="list">
            {items.length === 0 && <div className="empty-card">No open cases.</div>}
            {items.map((item) => (
              <button
                className={`case ${selected?.prediction_id === item.prediction_id ? "selected" : ""}`}
                key={item.prediction_id}
                onClick={() => setSelected(item)}
                type="button"
              >
                <span className="risk">
                  {formatRiskPercentage(item.fraud_probability)}
                </span>
                <span className="case-identity">
                  <strong>{item.transaction_id.slice(0, 13)}</strong>
                  <small>{new Date(item.scored_at).toLocaleString()}</small>
                </span>
                <span className="case-value">
                  <small>Amount</small>
                  <span className="amount">{Number(item.features.amount ?? 0).toFixed(2)}</span>
                </span>
              </button>
            ))}
          </div>
          <aside>
            {selected ? (
              <>
                <div className="detail-heading">
                  <div>
                    <p className="eyebrow"><span /> Case details</p>
                    <h2>
                      {formatRiskPercentage(selected.fraud_probability)} estimated fraud risk
                    </h2>
                    <small className="case-reference">Case {selected.prediction_id.slice(0, 8).toUpperCase()}</small>
                  </div>
                  <span className={`status ${selected.status.toLowerCase()}`}>
                    {selected.status}
                  </span>
                </div>
                <dl>
                  <div>
                    <dt>Amount</dt>
                    <dd>{String(selected.features.amount)}</dd>
                  </div>
                  <div>
                    <dt>Channel</dt>
                    <dd>{String(selected.features.channel)}</dd>
                  </div>
                  <div>
                    <dt>Category</dt>
                    <dd>{String(selected.features.merchant_category)}</dd>
                  </div>
                  <div>
                    <dt>Foreign</dt>
                    <dd>{selected.features.is_foreign ? "Yes" : "No"}</dd>
                  </div>
                  <div>
                    <dt>Review threshold</dt>
                    <dd>{formatRiskPercentage(selected.review_threshold)}</dd>
                  </div>
                  <div>
                    <dt>Block threshold</dt>
                    <dd>{formatRiskPercentage(selected.block_threshold)}</dd>
                  </div>
                  <div>
                    <dt>Model version</dt>
                    <dd>{selected.model_version}</dd>
                  </div>
                  <div>
                    <dt>Feature version</dt>
                    <dd>{selected.feature_version}</dd>
                  </div>
                </dl>
                <div className="policy-evidence">
                  <span>Why this case is here</span>
                  <strong>{selected.policy_reason.replaceAll("_", " ")}</strong>
                  <small>
                    Baseline risk {formatRiskPercentage(selected.explanation_base_value)};
                    bars below show calibrated probability-point changes.
                  </small>
                </div>
                <section className="investigator-actions" aria-label="Review outcome">
                  <div>
                    <p className="eyebrow"><span /> Your decision</p>
                    <h3>Complete this review</h3>
                    <p>
                      Your decision closes the case and becomes a verified outcome.
                    </p>
                  </div>
                  <label>
                    Notes (optional)
                    <textarea
                      value={notes}
                      onChange={(event) => setNotes(event.target.value)}
                      maxLength={2000}
                      placeholder="Evidence checked, customer verification, or reason…"
                    />
                  </label>
                  <div className="resolution-buttons">
                    <button
                      className="approve-button"
                      disabled={actionLoading !== null}
                      onClick={() => void resolveSelected("APPROVE")}
                      type="button"
                    >
                      {actionLoading === "APPROVE" ? "Approving…" : "Approve payment"}
                    </button>
                    <button
                      className="reject-button"
                      disabled={actionLoading !== null}
                      onClick={() => void resolveSelected("REJECT")}
                      type="button"
                    >
                      {actionLoading === "REJECT" ? "Saving…" : "Confirm fraud"}
                    </button>
                    <button
                      className="secondary-button"
                      disabled={actionLoading !== null || selected.status === "ESCALATED"}
                      onClick={() => void resolveSelected("ESCALATE")}
                      type="button"
                    >
                      {actionLoading === "ESCALATE" ? "Sending…" : "Send for another review"}
                    </button>
                  </div>
                </section>
                <div className="evidence-heading">
                  <span>Why this was flagged</span>
                  <small>Largest effect first</small>
                </div>
                <ShapExplanation values={selected.explanation} />
              </>
            ) : (
              <div className="empty-card">
                Choose a case to see the details.
              </div>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
