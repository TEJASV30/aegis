# Aegis limitations

Aegis is a production-inspired engineering demonstration, not a production fraud
control and not a claim of bank-grade performance.

## Evidence boundaries

- The champion is trained and tested on deterministic synthetic data. Synthetic
  accuracy does not transfer to a real institution.
- The external OpenML benchmark evaluates algorithm behavior on a public dataset;
  its anonymous features cannot validate Aegis's online feature store.
- Monetary loss avoided is simulated under manifest assumptions. It is not a
  causal estimate or realized benefit.
- Load-test numbers apply only to the hardware, dataset size, concurrency, build,
  and command recorded in the load report.
- Bootstrap intervals describe sampling variability within one fixed dataset;
  they do not cover distribution shift or data-generation uncertainty.

## Model and data limitations

The synthetic generator encodes recognizable attack patterns, so high metrics are
expected and should be interpreted as pipeline evidence. It omits graph features,
merchant/customer history beyond declared windows, adversarial adaptation,
chargeback delay mechanisms, and institutional policy changes. Calibration can
degrade when prevalence changes. Friendly fraud remains intrinsically difficult
without post-transaction and dispute context.

Customer age may create unjustified disparate behavior. Aegis reports age-band
segments and includes removal as a required real-world governance decision; no
fairness certification is claimed.

## Operational limitations

Compose uses local example credentials and a single host. It does not provide
high availability, multi-region failover, workload isolation, signed artifacts,
enterprise RBAC/SSO, HSM-backed keys, or PCI DSS controls. MLflow alias updates,
filesystem pointer swaps, and PostgreSQL state updates are coordinated with
verification and compensating rollback but are not a distributed transaction.

The current review capacity is a global active-case limit, not a staffing schedule
by investigator skill, shift, region, or service-level deadline. A production
queue needs assignment rules, appeals, dual control for sensitive actions, and
ongoing investigator-quality measurement.

## Required work before real use

Obtain governed real data and labels, document lawful use, perform privacy and
fairness reviews, establish decision ownership, validate calibration and policy
costs, run shadow traffic, execute failure/rollback drills, add secure identity
and secrets, establish database/artifact backups, and complete independent
security and model-risk reviews.
