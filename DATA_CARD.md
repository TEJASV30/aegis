# Aegis data card

## Intended use

Aegis uses a deterministic, temporally ordered synthetic transaction corpus to
exercise an end-to-end fraud decision system. It is suitable for software tests,
point-in-time feature verification, model comparison, calibration experiments,
human-review workflows, drift demonstrations, and reproducible load tests. It is
not evidence of performance on a bank, card network, merchant, or geography.

An independent evaluation harness also downloads the public credit-card fraud
benchmark identified as OpenML data ID 1597. That benchmark is evaluation
evidence only; it is not blended into the Aegis champion training set.

## Synthetic population

The generator creates ordered transactions with customer, merchant, device, and
behavioral context. Default cardinalities are 100,000 transactions, 8,000
customers, and 1,500 merchants across 120 days. Re-running the same seed produces
the same identifiers, event order, features, and labels.

Fraud archetypes are:

- account takeover: new/shared attack devices, failed attempts, remote channels,
  foreign activity, and high-value purchases;
- stolen card: card-not-present and point-of-sale abuse with mule-device bursts;
- friendly fraud: deliberately harder positives on familiar devices;
- legitimate edge cases: travel and new-device activity that overlaps with the
  fraud population.

The label is generated from a non-linear synthetic process and should be treated
as ground truth only inside this simulation.

## Schema and time semantics

All event timestamps must be timezone-aware and are normalized to UTC. Rolling
windows use `[event_time - window, event_time)`. The current event, future events,
and all equal-timestamp peers are excluded. Recent events without matured labels
remain in feature history but are excluded from training targets.

Currency is normalized to `amount_usd` before monetary aggregation using the
versioned `synthetic-fx-2025-01-v2` contract:

| Currency | Synthetic USD per unit |
|---|---:|
| USD | 1.0000 |
| EUR | 1.0800 |
| GBP | 1.2700 |
| INR | 0.0120 |
| JPY | 0.0067 |
| SGD | 0.7400 |

These constants are not market exchange rates. A real deployment must use an
effective-dated, governed foreign-exchange source and version it with the feature
contract.

## Quality controls

The Airflow source gate verifies minimum matured labels, time diversity,
supported currencies, non-negative canonical amounts, and exact currency
conversion parity. Dataset bytes and the feature schema are SHA-256 fingerprinted
in every release manifest. Offline/PostgreSQL parity tests cover equal-time and
window-boundary behavior.

## Representation, privacy, and fairness

Synthetic identifiers contain no real people or payment credentials. Customer
age is included only so segment diagnostics and removal experiments can reveal
whether it introduces unjustified disparities. The release process does not claim
fairness, representativeness, or regulatory suitability. Before real use, remove
age unless a documented lawful purpose and fairness review justify it; evaluate
performance across relevant protected and operational groups.

## Known limitations

The simulator cannot reproduce adversarial adaptation, delayed chargebacks,
issuer/merchant heterogeneity, seasonality, network effects, policy feedback,
real exchange-rate volatility, or real investigator error. Synthetic monetary
loss is a decision simulation based on declared assumptions, not causal business
impact.
