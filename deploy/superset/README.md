# Aegis Superset workspace

Docker provisions this workspace automatically after PostgreSQL migrations and
`superset init`. The idempotent provisioner is
[`bootstrap_aegis.py`](bootstrap_aegis.py); a restart updates the managed objects
in place instead of creating duplicates.

Open <http://localhost:8088/superset/dashboard/aegis-operations/> and sign in with
`admin` / `admin` for the local demo.

The managed workspace contains:

- one `Aegis Operational Store` PostgreSQL connection;
- datasets for hourly decisions and latency, investigation outcomes, matured
  model quality, drift reports, monitoring alerts, and release history;
- six saved evidence tables assembled into the published `Aegis Operations`
  dashboard.

PostgreSQL remains the evidence store, Airflow remains the orchestrator, and
Superset remains a read-only analytical surface. Performance charts must filter
by model and feature version, and only label-mature cohorts should be used for
quality claims. Drift and quality alerts require human review; they never trigger
automatic promotion.

For a non-demo deployment, replace the local password and secret key, use a
read-only analytics database role, enable SSO/RBAC and TLS, configure Content
Security Policy and a shared rate-limit backend, and configure a self-hosted SMTP
or notification transport if alert delivery is enabled.
