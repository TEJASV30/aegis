import { useCallback, useEffect, useState } from "react";
import { demoMode, platformApi } from "../api";
import {
  loadDemoOperationsState,
  resetDemoOperationsState,
  saveDemoOperationsState,
  simulateSyntheticBatch,
} from "../simulation";
import type {
  DataSummary,
  PlatformStatus,
  SyntheticGenerationResponse,
} from "../types";
import { Icon } from "./Icons";

type OperationsConsoleProps = {
  platform: PlatformStatus;
  onPlatformRefresh: () => Promise<void>;
};

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

type ServiceLabel = {
  name: string;
  purpose: string;
  meta: string;
  runtime: boolean;
};

const serviceLabels: Record<string, ServiceLabel> = {
  "Apache Airflow": {
    name: "Automated jobs",
    purpose: "View scheduled data, training, and monitoring jobs",
    meta: "Apache Airflow",
    runtime: true,
  },
  MLflow: {
    name: "Model versions",
    purpose: "Compare results and approved versions",
    meta: "MLflow",
    runtime: true,
  },
  PostgreSQL: {
    name: "Risk data",
    purpose: "Inspect the transaction, decision, and review data layer",
    meta: "PostgreSQL",
    runtime: true,
  },
  "Apache Superset": {
    name: "Reports and dashboards",
    purpose: "Explore risk, speed, outcomes, and data changes",
    meta: "Apache Superset",
    runtime: true,
  },
  FastAPI: {
    name: "Decision API",
    purpose: "Inspect and test the payment-checking API",
    meta: "FastAPI",
    runtime: true,
  },
  Prometheus: {
    name: "Service monitoring",
    purpose: "Review latency, throughput, errors, and health signals",
    meta: "Prometheus",
    runtime: true,
  },
  "Source code": {
    name: "Source code",
    purpose: "Review the complete open-source application",
    meta: "GitHub repository",
    runtime: false,
  },
  Architecture: {
    name: "Architecture",
    purpose: "Understand data, training, serving, and monitoring",
    meta: "Project documentation",
    runtime: false,
  },
  "Decision policy": {
    name: "Decision policy",
    purpose: "Review thresholds, capacity, and human decisions",
    meta: "Project documentation",
    runtime: false,
  },
  Limitations: {
    name: "Limitations",
    purpose: "Separate measured, simulated, and production claims",
    meta: "Project documentation",
    runtime: false,
  },
};

export function OperationsConsole({
  platform,
  onPlatformRefresh,
}: OperationsConsoleProps) {
  const [initialDemoState] = useState(() =>
    demoMode ? loadDemoOperationsState() : null,
  );
  const [summary, setSummary] = useState<DataSummary>(() =>
    initialDemoState?.summary ?? {
      raw_transactions: 0,
      labeled_transactions: 0,
      observed_fraud_rate: null,
      production_predictions: 0,
      open_reviews: 0,
      latest_event_timestamp: null,
    },
  );
  const [rows, setRows] = useState(initialDemoState?.rows ?? 100_000);
  const [days, setDays] = useState(initialDemoState?.days ?? 120);
  const [seed, setSeed] = useState(initialDemoState?.seed ?? 42);
  const [batch, setBatch] = useState<SyntheticGenerationResponse | null>(
    initialDemoState?.batch ?? null,
  );
  const [loading, setLoading] = useState(!demoMode);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refreshSummary = useCallback(async () => {
    if (demoMode) return;
    setLoading(true);
    try {
      setSummary(await platformApi.getDataSummary());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Data summary unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSummary();
  }, [refreshSummary]);

  async function generateBatch(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const result = demoMode
        ? simulateSyntheticBatch(rows, seed, days)
        : await platformApi.generateSyntheticData({
            rows,
            customers: Math.min(8_000, Math.max(50, Math.floor(rows / 5))),
            merchants: Math.min(1_500, Math.max(20, Math.floor(rows / 20))),
            days,
            seed,
            start: new Date(Date.now() - days * 86_400_000).toISOString(),
          });
      const existingFraudRows =
        summary.labeled_transactions * (summary.observed_fraud_rate ?? 0);
      const nextLabeledTransactions =
        summary.labeled_transactions + result.inserted_rows;
      const nextSummary: DataSummary = {
        ...summary,
        raw_transactions: summary.raw_transactions + result.inserted_rows,
        labeled_transactions: nextLabeledTransactions,
        latest_event_timestamp: result.event_end,
        observed_fraud_rate:
          nextLabeledTransactions === 0
            ? null
            : (existingFraudRows + result.fraud_rows) / nextLabeledTransactions,
      };
      setBatch(result);
      setSummary(nextSummary);
      if (demoMode) {
        const persisted = saveDemoOperationsState({
          schema_version: 1,
          summary: nextSummary,
          batch: result,
          rows,
          days,
          seed,
          saved_at: new Date().toISOString(),
        });
        setMessage(
          persisted
            ? "Test data saved in this browser."
            : "Test data generated for this session; browser storage is unavailable.",
        );
      }
      if (!demoMode) await refreshSummary();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Generation failed");
    } finally {
      setLoading(false);
    }
  }

  function resetDemoData() {
    const reset = resetDemoOperationsState();
    setSummary(reset.summary);
    setBatch(reset.batch);
    setRows(reset.rows);
    setDays(reset.days);
    setSeed(reset.seed);
    setError(null);
    setMessage("Hosted demo data reset to the baseline snapshot.");
  }

  async function reloadModel() {
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const loaded = await platformApi.reloadModel();
      await onPlatformRefresh();
      void loaded;
      setMessage(
        `Model ${loaded.model_version} is now active with features ${loaded.feature_version}.`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the approved model");
    } finally {
      setLoading(false);
    }
  }

  async function rollbackModel() {
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const loaded = await platformApi.rollbackModel();
      await onPlatformRefresh();
      setMessage(`Model ${loaded.model_version} is now active.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not switch models");
    } finally {
      setLoading(false);
    }
  }

  const metrics = [
    ["Transactions", formatNumber(summary.raw_transactions), "Stored payment history"],
    ["Verified outcomes", formatNumber(summary.labeled_transactions), "Known results"],
    [
      "Fraud rate",
      summary.observed_fraud_rate === null
        ? "—"
        : `${(summary.observed_fraud_rate * 100).toFixed(2)}%`,
      "Verified outcomes only",
    ],
    ["Payments checked", formatNumber(summary.production_predictions), "Live decisions"],
    ["Open reviews", formatNumber(summary.open_reviews), "Waiting for a person"],
  ];

  return (
    <section className="page-section">
      <div className="page-heading">
        <div>
          <p className="eyebrow"><span /> Overview</p>
          <h1>System health</h1>
          <p>Check data, model performance, and the services that keep Aegis running.</p>
        </div>
        <button
          className="secondary-button icon-button"
          onClick={() => void refreshSummary()}
          disabled={demoMode || loading}
          type="button"
        >
          <Icon name="refresh" /> Refresh
        </button>
      </div>

      {demoMode && (
        <div className="notice preview-notice">
          Hosted demo: only the browser experience runs on GitHub Pages. The repository
          still includes Airflow, MLflow, PostgreSQL, FastAPI, Superset, and Prometheus;
          start the Docker application to run those services together. System figures are
          an offline evaluation snapshot, and generated data is saved in this browser until
          you reset it.{" "}
          <a
            href="https://github.com/TEJASV30/aegis#one-command-demonstration"
            target="_blank"
            rel="noreferrer"
          >
            Run the complete platform
          </a>
          .
        </div>
      )}
      {error && <div className="notice error">{error}</div>}
      {message && <div className="notice success">{message}</div>}

      <div className="metric-grid">
        {metrics.map(([label, value, context], index) => (
          <div className="metric-card" key={label}>
            <div className="metric-card-top"><span>{label}</span><i>{String(index + 1).padStart(2, "0")}</i></div>
            <strong>{value}</strong>
            <small>{context}</small>
          </div>
        ))}
      </div>

      {platform.model_quality && (
        <div className="model-quality-card">
          <div className="model-quality-heading">
            <div>
              <p className="eyebrow"><span /> Model performance</p>
              <h2>Latest test results</h2>
            </div>
            <span className="quality-pass"><i /> All checks passed</span>
          </div>
          <div className="evaluation-scope">
            <div><span>Model version</span><strong>{platform.model_quality.model_version}</strong></div>
            <div><span>Feature version</span><strong>{platform.model_quality.feature_version}</strong></div>
            <div>
              <span>Test period</span>
              <strong>
                {formatDate(platform.model_quality.test_window_start)} –{" "}
                {formatDate(platform.model_quality.test_window_end)}
              </strong>
            </div>
            <div>
              <span>Test sample</span>
              <strong>
                {formatNumber(platform.model_quality.test_sample_size)} events ·{" "}
                {formatNumber(platform.model_quality.test_fraud_count)} fraud
              </strong>
            </div>
            <div><span>Data origin</span><strong>{platform.model_quality.data_origin}</strong></div>
          </div>
          <div className="quality-metric-grid">
            <div>
              <span>Accuracy</span>
              <strong>{(platform.model_quality.accuracy * 100).toFixed(1)}%</strong>
            </div>
            <div>
              <span>Precision-recall score</span>
              <strong>
                {(platform.model_quality.pr_auc_average_precision * 100).toFixed(1)}%
              </strong>
            </div>
            <div>
              <span>Recall at 1% false positives</span>
              <strong>
                {(platform.model_quality.recall_at_fixed_fpr * 100).toFixed(1)}%
              </strong>
            </div>
            <div>
              <span>Review queue precision</span>
              <strong>
                {(platform.model_quality.precision_at_k * 100).toFixed(1)}%
              </strong>
            </div>
            <div>
              <span>Calibration error</span>
              <strong>
                {(platform.model_quality.expected_calibration_error * 100).toFixed(2)}%
              </strong>
            </div>
            <div>
              <span>Balanced accuracy</span>
              <strong>
                {(platform.model_quality.balanced_accuracy * 100).toFixed(1)}%
              </strong>
            </div>
            <div>
              <span>Brier score</span>
              <strong>{platform.model_quality.brier_score.toFixed(4)}</strong>
            </div>
          </div>
          <details className="metric-definitions">
            <summary>How these numbers are calculated</summary>
            <dl>
              {Object.entries(platform.model_quality.metric_definitions).map(
                ([metric, definition]) => (
                  <div key={metric}>
                    <dt>{metric.replaceAll("_", " ")}</dt>
                    <dd>{definition}</dd>
                  </div>
                ),
              )}
            </dl>
            <p>
              Measured {formatDate(platform.model_quality.measured_at)}. Monetary impact
              is a simulation based on documented policy assumptions, not realized loss.
            </p>
          </details>
        </div>
      )}

      <div className="operations-grid">
        <form className="operation-card generator-card" onSubmit={(event) => void generateBatch(event)}>
          <div className="operation-icon"><Icon name="spark" /></div>
          <p className="eyebrow"><span /> Test data</p>
          <h2>Generate sample transactions</h2>
          <p>
            Create repeatable payment history for testing the complete workflow.
          </p>
          <div className="generator-fields">
            <label>
              Transactions
              <input
                aria-label="Synthetic rows"
                type="number"
                min="100"
                max="100000"
                value={rows}
                onChange={(event) => setRows(Number(event.target.value))}
              />
            </label>
            <label>
              History days
              <input
                aria-label="History days"
                type="number"
                min="7"
                max="730"
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
              />
            </label>
            <label>
              Random seed
              <input
                aria-label="Synthetic seed"
                type="number"
                min="0"
                value={seed}
                onChange={(event) => setSeed(Number(event.target.value))}
              />
            </label>
          </div>
          <div className="generator-actions">
            <button className="primary-button" type="submit" disabled={loading}>
              {loading ? "Generating…" : "Generate test data"}
            </button>
            {demoMode && (
              <button
                className="secondary-button"
                type="button"
                disabled={loading}
                onClick={resetDemoData}
              >
                Reset demo data
              </button>
            )}
          </div>
          {batch && (
            <div className="batch-result">
              <strong>{formatNumber(batch.inserted_rows)} transactions created</strong>
              <span>
                {batch.fraud_rows} fraud labels · {(batch.fraud_rate * 100).toFixed(2)}%
                fraud rate · seed {batch.seed}
              </span>
            </div>
          )}
        </form>

        <div className="operation-card">
          <div className="operation-icon"><Icon name="tower" /></div>
          <p className="eyebrow">
            <span /> {demoMode ? "Open-source stack" : "Connected tools"}
          </p>
          <h2>{demoMode ? "Platform services" : "Services"}</h2>
          {demoMode && (
            <p>
              The hosted page is the browser layer. Runtime services are present in the
              repository and launch through Docker.
            </p>
          )}
          <div className="service-list">
            {platform.services.map((service) => {
              const label = serviceLabels[service.name] ?? {
                name: service.name,
                purpose: service.purpose,
                meta: "Platform resource",
                runtime: false,
              };
              return (
                <a
                  href={service.url}
                  target="_blank"
                  rel="noreferrer"
                  key={service.name}
                  aria-label={`Open ${service.name}`}
                >
                  <span className="service-copy">
                    <strong>{label.name}</strong>
                    <small>{label.purpose}</small>
                    <span className="service-meta">
                      <i />
                      {label.meta}
                      {label.runtime && demoMode ? " · Runs with Docker" : ""}
                    </span>
                  </span>
                  <span className="service-arrow" aria-hidden="true"><Icon name="arrow" /></span>
                </a>
              );
            })}
          </div>
          {!demoMode && (
            <div className="release-actions">
              <button
                className="secondary-button reload-button"
                type="button"
                disabled={loading}
                onClick={() => void reloadModel()}
              >
                Use latest approved model
              </button>
              <button
                className="rollback-button"
                type="button"
                disabled={loading || !platform.previous_model_version}
                onClick={() => void rollbackModel()}
              >
                Use {platform.previous_model_version ?? "previous model"}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="flow-card">
        <p className="eyebrow"><span /> How it works</p>
        <div className="flow-heading"><h2>From payment to monitoring</h2><p>Each step is visible, testable, and independently operated.</p></div>
        <div className="flow-steps">
          <div>
            <span>01</span>
            <strong>Payment history</strong>
            <p>Stores payments, customer behavior, and review outcomes.</p>
          </div>
          <div>
            <span>02</span>
            <strong>Automated jobs</strong>
            <p>Builds features, tests models, and checks for data changes.</p>
          </div>
          <div>
            <span>03</span>
            <strong>Risk decision</strong>
            <p>Returns a probability, recommended action, and reasons.</p>
          </div>
          <div>
            <span>04</span>
            <strong>Monitoring</strong>
            <p>Tracks speed, outcomes, review volume, and data quality.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
