# Aegis model card

## Release identity

| Field | Value |
|---|---|
| Aegis release | `20260812T041508Z` |
| Feature contract | `v2` |
| Selected implementation | Calibrated LightGBM classifier |
| Artifact SHA-256 | `d904ac30488ec11066f5cf594ce06e9a6f0fcd17c166337ce393de7ab1ddcfc8` |
| Dataset SHA-256 | `936a98352d2526b00b3f1c99da15341f82686136094eb627c5195a94815fd364` |
| MLflow run | `36a2d1234bfb499d9dbf826c113d4838` |
| MLflow registry version | `2` |
| Data origin | Synthetic |

This card describes the immutable release produced by the verified Docker/Airflow
run on 2026-08-12. The machine-readable source of truth is the release
`manifest.json`; rendered evidence is in `reports/`.

## Intended use

The model demonstrates calibrated rare-event ranking, a three-way decision policy,
human-review capacity, explainability, and lifecycle controls. It is appropriate
for local engineering evaluation and portfolio demonstration. It must not be used
to make real financial decisions without governed real-world validation, lawful
use review, fairness analysis, monitoring design, and an accountable decision
owner.

## Training and evaluation data

The release used 405,008 matured labeled synthetic events from 2026-04-13 through
2026-08-12. Twenty-seven newer unlabeled events were retained as historical
feature context but excluded from training targets. Data generation mixes account
takeover, stolen card, harder friendly fraud, and legitimate high-risk edge cases.

See [DATA_CARD.md](DATA_CARD.md) for population, schema, currency, privacy, and
representation limitations.

## Evaluation protocol

Distinct UTC timestamps are split chronologically: the first 65% train the
candidate, the next 10% fit Platt calibration, the next 10% select the candidate
and thresholds, and the final 15% is untouched until selection is complete. Every
partition must contain both classes. Class weighting is learned from the training
partition only.

The selection headline is PR-AUC, not ROC-AUC or raw accuracy. Required gates are
accuracy ≥ 0.90, PR-AUC ≥ 0.65, and recall ≥ 0.65 at a fixed 1% false-positive
rate. Accuracy is retained as a gate because this synthetic distribution is
learnable, but it is never the sole ranking criterion.

## Candidate selection evidence

| Candidate | Accuracy | PR-AUC | Recall @ 1% FPR | Precision@500 | Brier | ECE |
|---|---:|---:|---:|---:|---:|---:|
| Logistic Regression | 0.9797 | 0.7809 | 0.7000 | 0.9820 | 0.0168 | 0.0033 |
| Isolation Forest | 0.9563 | 0.2019 | 0.0834 | 0.2800 | 0.0387 | 0.0127 |
| XGBoost | 0.9911 | 0.9161 | 0.8806 | 1.0000 | 0.0078 | 0.0017 |
| LightGBM | 0.9913 | 0.9208 | 0.8863 | 1.0000 | 0.0075 | 0.0019 |

The selected candidate maximized validation PR-AUC among those passing every gate.

## Untouched-test results

Test window: 2026-07-24 20:00:27 UTC through 2026-08-12 00:00:02
UTC; 60,754 events, including 2,746 fraud events.

| Metric | Estimate | Bootstrap 95% interval where available |
|---|---:|---:|
| Accuracy | 0.9924 | — |
| Balanced accuracy | 0.9292 | — |
| Fraud precision | 0.9684 | — |
| Fraud recall | 0.8598 | — |
| Fraud F1 | 0.9109 | — |
| PR-AUC / average precision | 0.9383 | 0.9303–0.9459 |
| Recall at 1% FPR | 0.9057 | 0.8947–0.9170 |
| Precision@500 | 1.0000 | — |
| Brier score | 0.0065 | 0.0060–0.0070 |
| Expected calibration error | 0.0015 | 0.0011–0.0022 |

Bootstrap intervals use 200 deterministic resamples of the fixed test period.
They capture sampling variability inside this synthetic corpus, not uncertainty
from distribution shift or the data-generating process.

## Calibration

Platt calibration was fitted on its own temporal partition. On the untouched test
period, calibration preserved PR-AUC at 0.9383, improved Brier score from 0.0117
to 0.0065, and improved ECE from 0.0270 to 0.0015.

## Decision policy

The validation-learned review threshold is 0.062919 and the block threshold is
0.999519. The fixed-FPR target is 1%, investigator capacity is 500 active cases,
and review-threshold fitting targets 90% fraud recall.

On the test period, 3,333 events crossed the review boundary; the capacity-aware
policy admitted 500 and explicitly suppressed 2,833 lower-risk candidates. The
separate model-classification accuracy must not be confused with policy accuracy,
because operational capacity intentionally changes the final action.

See [docs/DECISION_POLICY.md](docs/DECISION_POLICY.md).

## Feature ablation

| Feature set | PR-AUC | Recall @ 1% FPR | Precision@500 | Brier |
|---|---:|---:|---:|---:|
| Base only | 0.9349 | 0.9060 | 0.9960 | 0.0070 |
| Base + customer velocity | 0.9352 | 0.9024 | 0.9960 | 0.0071 |
| Base + customer/device velocity | 0.9383 | 0.9057 | 1.0000 | 0.0065 |

Velocity provides a small but measurable ranking and probability-error benefit.
The result does not support claiming a dramatic uplift.

## Segment and fairness evidence

The release evaluates channel, currency, merchant category, foreign/domestic,
fraud archetype, and age-band slices. Small or single-class segments are retained
with appropriate undefined metrics instead of being silently dropped. Detailed
tables are in [reports/segment_analysis.html](reports/segment_analysis.html).

Customer age may create unjustified disparate behavior. No fairness certification
is claimed. A real deployment should remove it unless a documented lawful purpose
and fairness review justify retention.

## Independent public benchmark

OpenML data ID 1597 provides 284,807 anonymized events and 492 fraud labels. The
untouched final source-order period contains 42,722 events and 52 fraud events.
The public copy omits the original elapsed-time field, so source order is preserved
but a timestamp-based split cannot be proven.

The strongest candidate reached PR-AUC 0.7571 and recall 0.8077 at 1% FPR. Two
candidate families performed poorly. These results are intentionally separate from
champion selection and demonstrate that synthetic ranking does not transfer
automatically. Full output: [reports/external_benchmark.json](reports/external_benchmark.json).

## Explanation semantics

SHAP is computed against the calibrated probability function, aggregated back to
raw input features. Values are probability deltas: base probability plus every
feature contribution reconstructs the final probability within a 0.002 absolute
tolerance. The UI says “percentage points”; it does not present raw margin or
log-odds values as probability changes.

Explanations describe model sensitivity, not causality or investigator proof.

## Monetary simulation

The reported simulated net monetary loss avoided is 546,924.88 canonical
synthetic USD units versus approving every event. Assumptions are: 100% recovery
for blocked fraud, 80% for reviewed fraud, 5 units per review, 3 units of
legitimate-review friction, and 25 units of legitimate-block friction. This is not
realized savings and not a causal estimate.

## Operational controls

Promotion requires passing metrics, schema/checksum agreement, model load,
calibration, threshold, and SHAP reconstruction gates. MLflow aliases, immutable
release bytes, atomic API reload, previous-release rollback, request idempotency,
capacity enforcement, and release-versioned predictions are executable and tested.

Matured-label monitoring calculates PR-AUC, fixed-FPR recall, calibration, and
decision yield by release. Drift or performance alerts require human review and
never trigger automatic promotion.

## Limitations and required review

This model has not been validated on an institution's data, does not model
adversarial adaptation or delayed chargebacks, and is not production secure by
default. Read [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for the complete boundary.
