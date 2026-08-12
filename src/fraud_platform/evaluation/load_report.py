"""Render durable load-test evidence from Locust CSV output."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _memory_gib() -> float | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (AttributeError, ValueError):
        return None


def _read_stats(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def render_report(
    stats_path: Path,
    output_path: Path,
    *,
    users: int,
    spawn_rate: float,
    duration: str,
    postgres_rows: int,
    model_version: str,
) -> dict[str, Any]:
    """Create an evidence-first HTML summary without hiding test conditions."""

    rows = _read_stats(stats_path)
    aggregate = next((row for row in rows if row.get("Name") == "Aggregated"), None)
    end_to_end = next(
        (row for row in rows if row.get("Name") == "end-to-end decision"), None
    )
    if aggregate is None or end_to_end is None:
        raise ValueError("Locust output is missing aggregate or end-to-end statistics.")
    evidence: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor() or "containerized CPU",
            "logical_cpu_count": os.cpu_count(),
            "memory_gib": _memory_gib(),
        },
        "postgres_transaction_rows": postgres_rows,
        "model_version": model_version,
        "concurrent_clients": users,
        "spawn_rate_per_second": spawn_rate,
        "duration": duration,
        "total_requests": int(float(end_to_end["Request Count"])),
        "failures": int(float(end_to_end["Failure Count"])),
        "success_rate": 1
        - int(float(end_to_end["Failure Count"]))
        / max(int(float(end_to_end["Request Count"])), 1),
        "requests_per_second": float(end_to_end["Requests/s"]),
        "end_to_end_latency_ms": {
            "p50": float(end_to_end["50%"]),
            "p95": float(end_to_end["95%"]),
            "p99": float(end_to_end["99%"]),
        },
        "raw_statistics": rows,
    }
    metric_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('Type', ''))}</td>"
        f"<td>{html.escape(row.get('Name', ''))}</td>"
        f"<td>{html.escape(row.get('Request Count', ''))}</td>"
        f"<td>{html.escape(row.get('Failure Count', ''))}</td>"
        f"<td>{html.escape(row.get('50%', ''))}</td>"
        f"<td>{html.escape(row.get('95%', ''))}</td>"
        f"<td>{html.escape(row.get('99%', ''))}</td>"
        f"<td>{html.escape(row.get('Requests/s', ''))}</td>"
        "</tr>"
        for row in rows
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Aegis load-test evidence</title>
<style>body{{font:15px system-ui;max-width:1180px;margin:40px auto;padding:0 24px;color:#15231d}}
h1{{font-size:2.4rem}} .scope{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.scope div{{border:1px solid #dce5df;border-radius:12px;padding:15px}} span{{color:#66786e;font-size:.78rem}}
strong{{display:block;margin-top:6px}} table{{width:100%;border-collapse:collapse;margin-top:24px;font-size:.82rem}}
th,td{{border-bottom:1px solid #dce5df;padding:10px;text-align:right}} th:nth-child(-n+2),td:nth-child(-n+2){{text-align:left}}
.note{{margin-top:24px;padding:16px;border-left:4px solid #167a5a;background:#eef8f2}}</style></head>
<body><p>Aegis · measured evidence</p><h1>Decision-serving load test</h1>
<div class="scope"><div><span>Concurrent clients</span><strong>{users}</strong></div>
<div><span>Decision requests</span><strong>{evidence['total_requests']}</strong></div>
<div><span>Success rate</span><strong>{evidence['success_rate']:.2%}</strong></div>
<div><span>Throughput</span><strong>{evidence['requests_per_second']:.2f} req/s</strong></div>
<div><span>End-to-end p50</span><strong>{evidence['end_to_end_latency_ms']['p50']:.1f} ms</strong></div>
<div><span>End-to-end p95</span><strong>{evidence['end_to_end_latency_ms']['p95']:.1f} ms</strong></div>
<div><span>End-to-end p99</span><strong>{evidence['end_to_end_latency_ms']['p99']:.1f} ms</strong></div>
<div><span>PostgreSQL rows</span><strong>{postgres_rows:,}</strong></div></div>
<p class="note">Measured {html.escape(evidence['measured_at'])} for release {html.escape(model_version)} over {html.escape(duration)}.
Hardware: {html.escape(str(evidence['hardware']))}. Component rows are server-reported timings carried in successful responses.</p>
<table><thead><tr><th>Type</th><th>Metric</th><th>Requests</th><th>Failures</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th><th>RPS</th></tr></thead>
<tbody>{metric_rows}</tbody></table></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    output_path.with_suffix(".json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/load_test.html"))
    parser.add_argument("--users", type=int, required=True)
    parser.add_argument("--spawn-rate", type=float, required=True)
    parser.add_argument("--duration", required=True)
    parser.add_argument("--postgres-rows", type=int, required=True)
    parser.add_argument("--model-version", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            render_report(
                args.stats,
                args.output,
                users=args.users,
                spawn_rate=args.spawn_rate,
                duration=args.duration,
                postgres_rows=args.postgres_rows,
                model_version=args.model_version,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
