import { useEffect, useState } from "react";
import { demoMode, fallbackPlatformStatus, platformApi } from "./api";
import { OperationsConsole } from "./components/OperationsConsole";
import { ReviewQueue } from "./components/ReviewQueue";
import { TransactionWorkbench } from "./components/TransactionWorkbench";
import { generateReviewItems } from "./simulation";
import { Icon } from "./components/Icons";
import type { InvestigationAction, PlatformStatus, ReviewItem } from "./types";

type Page = "score" | "queue" | "operations";
type Theme = "dark" | "light";

function getInitialTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function App() {
  const [page, setPage] = useState<Page>("score");
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [platform, setPlatform] = useState<PlatformStatus>(fallbackPlatformStatus());
  const [previewItems, setPreviewItems] = useState<ReviewItem[]>(() =>
    demoMode ? generateReviewItems(6) : [],
  );

  async function refreshPlatform() {
    if (demoMode) return;
    try {
      setPlatform(await platformApi.getStatus());
    } catch {
      setPlatform(fallbackPlatformStatus());
    }
  }

  useEffect(() => {
    void refreshPlatform();
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("aegis-theme", theme);
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "dark" ? "#0b1020" : "#f4f6fa");
  }, [theme]);

  function addReview(item: ReviewItem) {
    setPreviewItems((current) =>
      [item, ...current].sort(
        (left, right) => right.fraud_probability - left.fraud_probability,
      ),
    );
  }

  function resolvePreview(predictionId: string, action: InvestigationAction) {
    setPreviewItems((current) =>
      action === "ESCALATE"
        ? current.map((item) =>
            item.prediction_id === predictionId
              ? { ...item, status: "ESCALATED", assignee: "local-investigator" }
              : item,
          )
        : current.filter((item) => item.prediction_id !== predictionId),
    );
  }

  return (
    <div className="app-shell" data-page={page}>
      <div className="canvas-grain" aria-hidden="true" />
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <div className="ambient ambient-three" aria-hidden="true" />
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <span className="brand-core">A</span>
            <span className="brand-orbit" />
          </span>
          <span>
            <strong>Aegis</strong>
            <small>Payment risk, made clear</small>
          </span>
        </div>
        <nav aria-label="Platform sections">
          <button
            aria-current={page === "score" ? "page" : undefined}
            className={page === "score" ? "active" : ""}
            onClick={() => setPage("score")}
          >
            <Icon name="pulse" />
            Check payment
          </button>
          <button
            aria-current={page === "queue" ? "page" : undefined}
            className={page === "queue" ? "active" : ""}
            onClick={() => setPage("queue")}
          >
            <Icon name="cases" />
            Review cases
            {demoMode && previewItems.length > 0 && <span>{previewItems.length}</span>}
          </button>
          <button
            aria-current={page === "operations" ? "page" : undefined}
            className={page === "operations" ? "active" : ""}
            onClick={() => setPage("operations")}
          >
            <Icon name="tower" />
            System health
          </button>
        </nav>
        <div className="header-controls">
          <button
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            aria-pressed={theme === "light"}
            className="theme-toggle"
            onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            type="button"
          >
            <span className="theme-toggle-icon">
              <Icon name={theme === "dark" ? "moon" : "sun"} />
            </span>
            <span className="theme-toggle-label">
              {theme === "dark" ? "Dark" : "Light"}
            </span>
          </button>
          <div className={`mode-pill ${demoMode ? "preview" : platform.status}`}>
            <span />
            {demoMode
              ? "Hosted demo"
              : platform.model_loaded
                ? "Protection is active"
                : "Setup needed"}
          </div>
        </div>
      </header>

      <main className="page-transition" key={page}>
        {page === "score" && (
          <TransactionWorkbench
            modelLoaded={platform.model_loaded}
            onReviewCreated={addReview}
          />
        )}
        {page === "queue" && (
          <ReviewQueue
            previewMode={demoMode}
            previewItems={previewItems}
            onRegeneratePreview={() => setPreviewItems(generateReviewItems(6))}
            onResolvePreview={resolvePreview}
          />
        )}
        {page === "operations" && (
          <OperationsConsole
            platform={platform}
            onPlatformRefresh={refreshPlatform}
          />
        )}
      </main>
    </div>
  );
}
