#!/usr/bin/env bash
set -euo pipefail

wait_for_run() {
  local dag_id="$1"
  local run_id="$2"
  local state="queued"
  local attempts=0

  while [[ "$state" == "queued" || "$state" == "running" || -z "$state" ]]; do
    if (( attempts >= 240 )); then
      printf 'Timed out waiting for %s after 20 minutes.\n' "$dag_id" >&2
      return 1
    fi
    sleep 5
    attempts=$((attempts + 1))
    state="$(
      docker compose exec -T postgres psql -U fraud -d airflow -tAc \
        "SELECT state FROM dag_run WHERE dag_id = '${dag_id}' AND run_id = '${run_id}'"
    )"
    state="${state//[[:space:]]/}"
    printf '%s: %s\n' "$dag_id" "${state:-waiting}"
  done

  if [[ "$state" != "success" ]]; then
    docker compose exec -T airflow-scheduler \
      airflow tasks states-for-dag-run "$dag_id" "$run_id"
    return 1
  fi
}

wait_for_dag() {
  local dag_id="$1"
  local attempts=0
  local discovered="0"
  while [[ "$discovered" != "1" ]]; do
    if (( attempts >= 60 )); then
      printf 'Airflow did not discover %s within five minutes.\n' "$dag_id" >&2
      return 1
    fi
    attempts=$((attempts + 1))
    sleep 5
    discovered="$(
      docker compose exec -T postgres psql -U fraud -d airflow -tAc \
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM dag WHERE dag_id = '${dag_id}' AND is_active) THEN 1 ELSE 0 END"
    )"
    discovered="${discovered//[[:space:]]/}"
  done
}

export AEGIS_AIRFLOW_DEMO_MODE=true
mkdir -p artifacts data reports
docker compose up -d --build
wait_for_dag fraud_synthetic_bootstrap
wait_for_dag fraud_model_training

bootstrap_run="demo_bootstrap_$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T airflow-scheduler \
  airflow dags trigger fraud_synthetic_bootstrap --run-id "$bootstrap_run"
docker compose exec -T airflow-scheduler \
  airflow dags unpause fraud_synthetic_bootstrap
wait_for_run fraud_synthetic_bootstrap "$bootstrap_run"
docker compose exec -T airflow-scheduler \
  airflow dags pause fraud_synthetic_bootstrap

training_run="demo_training_$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T airflow-scheduler \
  airflow dags trigger fraud_model_training --run-id "$training_run"
docker compose exec -T airflow-scheduler \
  airflow dags unpause fraud_model_training
wait_for_run fraud_model_training "$training_run"
docker compose exec -T airflow-scheduler \
  airflow dags pause fraud_model_training

docker compose exec -T api python -c \
  'import urllib.request; request = urllib.request.Request("http://127.0.0.1:8000/v1/model/reload", method="POST"); print(urllib.request.urlopen(request, timeout=30).read().decode())'
./scripts/verify_superset.sh

printf '\nAegis is ready:\n'
printf '  Investigator console: http://localhost:3000\n'
printf '  API documentation:    http://localhost:8000/docs\n'
printf '  Airflow:               http://localhost:8080\n'
printf '  MLflow:                http://localhost:5001\n'
printf '  Superset dashboard:    http://localhost:8088/superset/dashboard/aegis-operations/\n'
printf '  Prometheus:            http://localhost:9090\n'
