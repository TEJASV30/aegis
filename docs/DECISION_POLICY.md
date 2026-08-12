# Aegis decision policy

## Policy objective

Aegis separates model ranking from operational action. A calibrated probability
is an estimated likelihood under the evaluation distribution; it is not a legal
finding or proof of fraud. Learned thresholds convert that probability into:

- **Approve**: below the review threshold, or explicitly suppressed because the
  bounded queue is full of higher-risk work;
- **Manual review**: at or above the review threshold, below the block threshold,
  and admitted within active investigator capacity;
- **Block**: at or above the block threshold.

## Threshold derivation

Thresholds are fitted only on the newer half of the temporal validation period.
The review threshold selects the highest-precision cutoff that meets the declared
fraud-recall target. The block threshold is the stricter of the cutoff satisfying
the fixed false-positive-rate tolerance and the high-risk capacity cutoff. The
untouched test period never selects a candidate or threshold.

`Precision@K` measures risk ranking at the declared investigator capacity. It is
not capacity enforcement. Serving separately enforces a maximum active queue in
PostgreSQL under an advisory lock; automatic blocks do not consume that capacity.

## Human action

An investigator may approve, reject, or escalate a case. Approval records a
legitimate verified outcome; rejection records confirmed fraud; escalation keeps
the case in an escalated state without inventing a label. Notes, assignee, update
time, prediction identity, and release identity are retained.

When a champion changes, unresolved cases from older releases are marked as
release-superseded instead of silently mixing incompatible policies in the active
queue.

## Explanation semantics

Explanations are SHAP contributions in calibrated probability units. The base
probability plus every contribution reconstructs the final calibrated probability
within a tested numerical tolerance. The UI expresses contributions as percentage
point changes, not percentages and not raw log-odds. Explanations describe the
model response; they do not establish causality.

## Cost simulation

The release manifest contains explicit assumptions for fraud recovery, review
cost, legitimate-customer review friction, and legitimate block friction. The
reported monetary-loss avoided metric compares the policy with approving every
transaction. It is simulated and must never be described as realized savings.

## Governance rules

- No promotion occurs solely because drift was detected.
- No candidate may use test-period results for selection.
- No unlabeled event may be coerced into a legitimate label.
- Every prediction must retain model, feature, checksum, threshold, and policy
  reason evidence.
- Threshold or capacity changes require documented owner approval and a replay of
  the evaluation suite.
