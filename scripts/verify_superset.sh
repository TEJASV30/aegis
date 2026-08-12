#!/usr/bin/env bash
set -euo pipefail

actual="$({
  docker compose exec -T postgres psql -U fraud -d superset -At -F '|' -c "
    SELECT
      (SELECT COUNT(*) FROM dbs
       WHERE database_name = 'Aegis Operational Store'),
      (SELECT COUNT(*) FROM tables AS t
       JOIN dbs AS d ON d.id = t.database_id
       WHERE d.database_name = 'Aegis Operational Store'),
      (SELECT COUNT(*) FROM slices
       WHERE slice_name IN (
         'Decision Flow and Latency',
         'Investigation Outcomes',
         'Matured Quality Evidence',
         'Drift Reports',
         'Monitoring Alerts',
         'Decision Core Release History'
       )),
      (SELECT COUNT(*) FROM dashboards
       WHERE slug = 'aegis-operations' AND published),
      (SELECT COUNT(*) FROM dashboard_slices AS ds
       JOIN dashboards AS d ON d.id = ds.dashboard_id
       WHERE d.slug = 'aegis-operations');
  "
} | tr -d '[:space:]')"

expected="1|6|6|1|6"
if [[ "$actual" != "$expected" ]]; then
  printf 'Superset workspace mismatch: expected %s, received %s\n' \
    "$expected" "$actual" >&2
  exit 1
fi

printf 'Superset workspace verified: %s\n' "$actual"
