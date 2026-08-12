#!/usr/bin/env bash
set -euo pipefail

users="${AEGIS_LOAD_USERS:-10}"
spawn_rate="${AEGIS_LOAD_SPAWN_RATE:-2}"
duration="${AEGIS_LOAD_DURATION:-30s}"

mkdir -p reports/load-test
docker compose --profile benchmark run --rm locust \
  -f /mnt/locust/locustfile.py --host http://api:8000 --headless \
  --users "${users}" --spawn-rate "${spawn_rate}" --run-time "${duration}" \
  --csv /reports/load-test/locust --html /reports/load-test/locust-native.html

postgres_rows="$(docker compose exec -T postgres psql -U fraud -d fraud -tAc 'SELECT COUNT(*) FROM raw_transactions')"
model_version="$(docker compose exec -T postgres psql -U fraud -d fraud -tAc "SELECT active_model_version FROM model_release_state WHERE singleton")"
docker compose run --rm --no-deps airflow-scheduler python \
  -m fraud_platform.evaluation.load_report \
  --stats /opt/airflow/reports/load-test/locust_stats.csv \
  --output /opt/airflow/reports/load_test.html \
  --users "${users}" --spawn-rate "${spawn_rate}" --duration "${duration}" \
  --postgres-rows "${postgres_rows}" --model-version "${model_version}"
