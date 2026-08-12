"""Render release evidence into durable, self-contained HTML reports."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

METRICS = (
    "accuracy",
    "balanced_accuracy",
    "pr_auc_average_precision",
    "recall_at_fixed_fpr",
    "precision_at_k",
    "brier_score",
    "expected_calibration_error",
)


def _value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return html.escape(str(value))


def _page(title: str, subtitle: str, content: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:15px system-ui;max-width:1240px;margin:40px auto;padding:0 24px;color:#15231d}}h1{{font-size:2.5rem;margin-bottom:6px}}
h2{{margin-top:32px}}p{{color:#617269;line-height:1.55}}table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th,td{{border-bottom:1px solid #dce5df;padding:10px;text-align:right}}th:first-child,td:first-child{{text-align:left}}
.scope{{padding:16px;border-left:4px solid #167a5a;background:#eef8f2}}code{{font-size:.82em}}</style></head>
<body><p>Aegis · reproducible evidence</p><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p>{content}</body></html>"""


def _metric_table(rows: dict[str, dict[str, Any]]) -> str:
    head = "".join(f"<th>{html.escape(metric.replace('_', ' '))}</th>" for metric in METRICS)
    body = "".join(
        "<tr>"
        f"<td>{html.escape(name.replace('_', ' '))}</td>"
        + "".join(f"<td>{_value(metrics.get(metric))}</td>" for metric in METRICS)
        + "</tr>"
        for name, metrics in rows.items()
    )
    return f"<table><thead><tr><th>Candidate</th>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_model_reports(
    manifest_path: Path,
    comparison_output: Path,
    segment_output: Path,
    external_path: Path | None = None,
) -> None:
    """Render selection, untouched-test, calibration, ablation, and segment evidence."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    window = manifest["test_window"]
    scope = (
        "<div class='scope'>"
        f"Release <code>{html.escape(manifest['model_version'])}</code> · feature contract <code>{html.escape(manifest['feature_version'])}</code><br>"
        f"Untouched test: {html.escape(window['start'])} to {html.escape(window['end'])} · {window['rows']:,} events · {window['fraud_rows']:,} fraud.<br>"
        "Ranking, calibration, and threshold evidence is measured. Monetary loss is explicitly simulated."
        "</div>"
    )
    calibration = manifest["calibration_comparison"]
    calibration_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{values['pr_auc_average_precision']:.4f}</td><td>{values['brier_score']:.4f}</td><td>{values['expected_calibration_error']:.4f}</td></tr>"
        for name, values in calibration.items()
    )
    content = (
        scope
        + "<h2>Candidate selection-period comparison</h2>"
        + _metric_table(manifest["candidate_comparison"])
        + "<h2>Selected release on untouched test period</h2>"
        + _metric_table({manifest["model_name"]: manifest["test_metrics"]})
        + "<h2>Feature ablation on untouched test period</h2>"
        + _metric_table(manifest["feature_ablation"])
        + "<h2>Calibration comparison</h2><table><thead><tr><th>State</th><th>PR-AUC</th><th>Brier</th><th>ECE</th></tr></thead>"
        + f"<tbody>{calibration_rows}</tbody></table>"
        + "<h2>Bootstrap 95% intervals</h2><pre>"
        + html.escape(json.dumps(manifest["bootstrap_95_intervals"], indent=2))
        + "</pre>"
    )
    if external_path and external_path.exists():
        external = json.loads(external_path.read_text(encoding="utf-8"))
        external_rows = {
            name: result["metrics"] for name, result in external["results"].items()
        }
        content += (
            "<h2>Independent public benchmark</h2>"
            f"<p>{html.escape(external['benchmark'])}, OpenML data ID {external['openml_data_id']}; "
            f"{external['test_rows']:,} untouched test rows and {external['test_fraud_rows']:,} fraud rows.</p>"
            + _metric_table(external_rows)
        )
    comparison_output.parent.mkdir(parents=True, exist_ok=True)
    comparison_output.write_text(
        _page("Model comparison", "Temporal evaluation and release evidence", content),
        encoding="utf-8",
    )

    segments = manifest["segment_metrics"]
    segment_sections: list[str] = [scope]
    segment_metrics = (
        "sample_count",
        "fraud_count",
        "fraud_rate",
        "pr_auc_average_precision",
        "recall_at_fixed_fpr",
        "brier_score",
        "expected_calibration_error",
    )
    for dimension, groups in segments.items():
        headers = "".join(f"<th>{name.replace('_', ' ')}</th>" for name in segment_metrics)
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(group)}</td>"
            + "".join(f"<td>{_value(values.get(name))}</td>" for name in segment_metrics)
            + "</tr>"
            for group, values in groups.items()
        )
        segment_sections.append(
            f"<h2>{html.escape(dimension.replace('_', ' ').title())}</h2>"
            f"<table><thead><tr><th>Segment</th>{headers}</tr></thead><tbody>{rows}</tbody></table>"
        )
    segment_output.write_text(
        _page(
            "Segment analysis",
            "Operational, behavioral, and age-band quality slices",
            "".join(segment_sections),
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--comparison-output", type=Path, default=Path("reports/model_comparison.html"))
    parser.add_argument("--segment-output", type=Path, default=Path("reports/segment_analysis.html"))
    parser.add_argument("--external", type=Path, default=Path("reports/external_benchmark.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    render_model_reports(
        args.manifest,
        args.comparison_output,
        args.segment_output,
        args.external,
    )


if __name__ == "__main__":
    main()
