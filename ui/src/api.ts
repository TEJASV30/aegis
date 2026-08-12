import type {
  DataSummary,
  DecisionResponse,
  InvestigationAction,
  InvestigationResolution,
  PlatformStatus,
  ReviewItem,
  SyntheticGenerationResponse,
  TransactionInput,
} from "./types";

// Production serves the API through the same origin. This avoids browser CORS
// differences and keeps public deployments behind a single exposed port.
export const apiUrl = import.meta.env.VITE_API_URL ?? "/api";
export const demoMode = import.meta.env.VITE_DEMO_MODE === "true";

const host = window.location.hostname || "localhost";
const repositoryUrl = "https://github.com/TEJASV30/aegis";

export function fallbackPlatformStatus(): PlatformStatus {
  if (demoMode) {
    return {
      status: "ready",
      model_loaded: true,
      model_name: "Aegis risk engine",
      model_version: "20260812T064350Z",
      feature_version: "v2",
      artifact_checksum: "hosted-demo-snapshot",
      previous_model_version: null,
      model_quality: {
        gate_status: "passed",
        model_version: "20260812T064350Z",
        feature_version: "v2",
        test_window_start: "2026-07-20T06:55:50.423770Z",
        test_window_end: "2026-08-12T06:33:51.442000Z",
        test_sample_size: 75_755,
        test_fraud_count: 3_404,
        data_origin: "synthetic",
        measured_at: "2026-08-12T06:44:02.148112Z",
        metric_definitions: {
          accuracy:
            "Binary accuracy at a fixed 0.5 cutoff on the untouched temporal test period.",
          balanced_accuracy:
            "Mean recall across legitimate and fraudulent transactions at a 0.5 cutoff.",
          pr_auc_average_precision:
            "Average precision across the precision-recall curve.",
          recall_at_fixed_fpr:
            "Fraud recall without exceeding a 1% legitimate false-positive rate.",
          precision_at_k:
            "Fraud prevalence within the capacity-limited highest-risk review cases.",
          brier_score: "Mean squared error of calibrated probabilities.",
          expected_calibration_error:
            "Weighted confidence-versus-outcome gap across ten probability bins.",
          simulated_net_monetary_loss_avoided:
            "Scenario estimate under documented recovery and friction assumptions.",
        },
        accuracy: 0.9925813477658241,
        balanced_accuracy: 0.9332672633530679,
        pr_auc_average_precision: 0.9504007786230719,
        recall_at_fixed_fpr: 0.917743830787309,
        precision_at_k: 1,
        brier_score: 0.006351551393815856,
        expected_calibration_error: 0.0006601335248788128,
        simulated_net_monetary_loss_avoided: 620_740.608976,
      },
      synthetic_generation_enabled: true,
      services: [
        {
          name: "Apache Airflow",
          url: `${repositoryUrl}/tree/main/airflow`,
          purpose: "Orchestrates data, feature, training, and monitoring workflows",
        },
        {
          name: "MLflow",
          url: `${repositoryUrl}/tree/main/src/fraud_platform/models`,
          purpose: "Tracks candidate runs, releases, promotion, and rollback",
        },
        {
          name: "PostgreSQL",
          url: `${repositoryUrl}/tree/main/deploy/postgres`,
          purpose: "Stores transactions, predictions, reviews, and outcomes",
        },
        {
          name: "FastAPI",
          url: `${repositoryUrl}/blob/main/src/fraud_platform/serving/main.py`,
          purpose: "Serves idempotent decisions, explanations, and review actions",
        },
        {
          name: "Apache Superset",
          url: `${repositoryUrl}/tree/main/deploy/superset`,
          purpose: "Presents operational risk, latency, outcome, and drift dashboards",
        },
        {
          name: "Prometheus",
          url: `${repositoryUrl}/tree/main/deploy/prometheus`,
          purpose: "Collects request, latency, throughput, and health signals",
        },
        {
          name: "Source code",
          url: repositoryUrl,
          purpose: "Review the complete open-source application",
        },
        {
          name: "Architecture",
          url: `${repositoryUrl}/blob/main/docs/ARCHITECTURE.md`,
          purpose: "Understand data, training, serving, and monitoring",
        },
        {
          name: "Decision policy",
          url: `${repositoryUrl}/blob/main/docs/DECISION_POLICY.md`,
          purpose: "Review thresholds, capacity, and human decisions",
        },
        {
          name: "Limitations",
          url: `${repositoryUrl}/blob/main/docs/LIMITATIONS.md`,
          purpose: "Separate measured, simulated, and production claims",
        },
      ],
    };
  }

  return {
    status: "degraded",
    model_loaded: false,
    model_name: null,
    model_version: null,
    feature_version: null,
    artifact_checksum: null,
    previous_model_version: null,
    model_quality: null,
    synthetic_generation_enabled: true,
    services: [
      {
        name: "Apache Airflow",
        url: import.meta.env.VITE_AIRFLOW_URL ?? `http://${host}:8080`,
        purpose: "Run feature, training, and drift DAGs",
      },
      {
        name: "MLflow",
        url: import.meta.env.VITE_MLFLOW_URL ?? `http://${host}:5001`,
        purpose: "Compare experiments, metrics, and artifacts",
      },
      {
        name: "Apache Superset",
        url: import.meta.env.VITE_SUPERSET_URL ?? `http://${host}:8088`,
        purpose: "Explore risk, queue, latency, and drift dashboards",
      },
      {
        name: "FastAPI",
        url: `${apiUrl}/docs`,
        purpose: "Inspect and exercise the serving API",
      },
    ],
  };
}

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string; message?: string };
      message = body.message ?? body.detail ?? message;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const platformApi = {
  getStatus: () => fetchJson<PlatformStatus>("/v1/platform"),
  getDataSummary: () => fetchJson<DataSummary>("/v1/data-summary"),
  getReviewQueue: () => fetchJson<ReviewItem[]>("/v1/review-queue?limit=100"),
  resolveInvestigation: (
    predictionId: string,
    action: InvestigationAction,
    notes: string,
  ) =>
    fetchJson<InvestigationResolution>(`/v1/investigations/${predictionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        assignee: "local-investigator",
        notes: notes.trim() || null,
      }),
    }),
  reloadModel: () =>
    fetchJson<{
      model_name: string;
      model_version: string;
      feature_version: string;
      artifact_checksum: string;
      previous_model_version: string | null;
    }>("/v1/model/reload", {
      method: "POST",
    }),
  rollbackModel: () =>
    fetchJson<{
      model_name: string;
      model_version: string;
      feature_version: string;
      artifact_checksum: string;
      previous_model_version: string | null;
    }>("/v1/model/rollback", {
      method: "POST",
    }),
  score: (transaction: TransactionInput) =>
    fetchJson<DecisionResponse>("/v1/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(transaction),
    }),
  generateSyntheticData: (request: {
    rows: number;
    customers: number;
    merchants: number;
    days: number;
    seed: number;
    start: string;
  }) =>
    fetchJson<SyntheticGenerationResponse>("/v1/synthetic-data", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
};
