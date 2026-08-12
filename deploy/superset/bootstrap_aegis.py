"""Idempotently provision the Aegis operational analytics workspace.

This script runs inside the pinned Superset container after ``superset init``.
It creates the PostgreSQL connection, physical datasets, saved charts, and a
published dashboard. Re-running it updates the managed objects in place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from superset.app import create_app


@dataclass(frozen=True)
class DatasetDefinition:
    """Metadata needed to expose one PostgreSQL relation in Superset."""

    table_name: str
    chart_name: str
    main_dttm_col: str | None
    columns: tuple[str, ...]
    row_limit: int = 100


DATABASE_NAME = "Aegis Operational Store"
DATABASE_URI = os.getenv(
    "AEGIS_ANALYTICS_DSN",
    "postgresql+psycopg2://fraud:fraud@postgres:5432/fraud",
)
DASHBOARD_TITLE = "Aegis Operations"
DASHBOARD_SLUG = "aegis-operations"

DATASETS = (
    DatasetDefinition(
        table_name="v_fraud_operations_hourly",
        chart_name="Decision Flow and Latency",
        main_dttm_col="hour",
        columns=(
            "hour",
            "model_version",
            "transaction_count",
            "approved_count",
            "reviewed_count",
            "blocked_count",
            "queue_admitted_count",
            "average_fraud_probability",
            "observed_fraud_rate",
            "p95_feature_latency_ms",
            "p95_model_latency_ms",
            "p95_inference_latency_ms",
        ),
    ),
    DatasetDefinition(
        table_name="v_investigator_queue",
        chart_name="Investigation Outcomes",
        main_dttm_col="scored_at",
        columns=(
            "scored_at",
            "transaction_id",
            "fraud_probability",
            "status",
            "disposition",
            "assignee",
            "model_version",
            "feature_version",
            "policy_reason",
        ),
    ),
    DatasetDefinition(
        table_name="v_model_performance_latest",
        chart_name="Matured Quality Evidence",
        main_dttm_col="generated_at",
        columns=(
            "generated_at",
            "model_version",
            "feature_version",
            "cohort_start",
            "cohort_end",
            "maturity_cutoff",
            "sample_count",
            "fraud_count",
            "metrics",
            "alert_status",
        ),
    ),
    DatasetDefinition(
        table_name="drift_reports",
        chart_name="Drift Reports",
        main_dttm_col="generated_at",
        columns=(
            "generated_at",
            "reference_start",
            "reference_end",
            "current_start",
            "current_end",
            "dataset_drift",
            "drifted_feature_count",
            "metrics",
        ),
    ),
    DatasetDefinition(
        table_name="monitoring_alerts",
        chart_name="Monitoring Alerts",
        main_dttm_col="created_at",
        columns=(
            "created_at",
            "alert_type",
            "severity",
            "model_version",
            "message",
            "status",
            "resolved_at",
        ),
    ),
    DatasetDefinition(
        table_name="model_releases",
        chart_name="Decision Core Release History",
        main_dttm_col="created_at",
        columns=(
            "created_at",
            "activated_at",
            "model_version",
            "feature_version",
            "status",
            "mlflow_run_id",
            "mlflow_model_version",
            "artifact_checksum",
        ),
    ),
)


def _chart_params(dataset_id: int, definition: DatasetDefinition) -> str:
    """Return conservative raw-table chart parameters for Superset 4.1."""

    payload: dict[str, Any] = {
        "adhoc_filters": [],
        "all_columns": list(definition.columns),
        "datasource": f"{dataset_id}__table",
        "include_search": True,
        "order_by_cols": [],
        "percent_metrics": [],
        "query_mode": "raw",
        "row_limit": definition.row_limit,
        "server_page_length": 25,
        "table_filter": True,
        "time_range": "No filter",
        "viz_type": "table",
    }
    return json.dumps(payload, sort_keys=True)


def _dashboard_position(charts: list[Any]) -> str:
    """Build a deterministic two-column dashboard layout."""

    row_ids: list[str] = []
    position: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "HEADER_ID": {
            "id": "HEADER_ID",
            "meta": {"text": DASHBOARD_TITLE},
            "type": "HEADER",
        },
        "ROOT_ID": {
            "children": ["GRID_ID"],
            "id": "ROOT_ID",
            "type": "ROOT",
        },
    }

    for row_index in range(0, len(charts), 2):
        row_id = f"ROW-aegis-{row_index // 2 + 1}"
        row_ids.append(row_id)
        chart_ids: list[str] = []
        for chart in charts[row_index : row_index + 2]:
            chart_id = f"CHART-aegis-{chart.id}"
            chart_ids.append(chart_id)
            position[chart_id] = {
                "children": [],
                "id": chart_id,
                "meta": {
                    "chartId": chart.id,
                    "height": 48,
                    "sliceName": chart.slice_name,
                    "uuid": str(chart.uuid),
                    "width": 6,
                },
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "type": "CHART",
            }
        position[row_id] = {
            "children": chart_ids,
            "id": row_id,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
            "type": "ROW",
        }

    position["GRID_ID"] = {
        "children": row_ids,
        "id": "GRID_ID",
        "parents": ["ROOT_ID"],
        "type": "GRID",
    }
    return json.dumps(position, sort_keys=True)


def provision() -> None:
    """Create or update all Aegis-owned Superset objects."""

    app = create_app()
    with app.app_context():
        from superset import db
        from superset.connectors.sqla.models import SqlaTable
        from superset.models.core import Database
        from superset.models.dashboard import Dashboard
        from superset.models.slice import Slice
        from superset.utils.core import DatasourceType

        admin = app.appbuilder.sm.find_user(username="admin")

        database = (
            db.session.query(Database)
            .filter(Database.database_name == DATABASE_NAME)
            .one_or_none()
        )
        if database is None:
            database = Database(database_name=DATABASE_NAME)
            db.session.add(database)
        database.sqlalchemy_uri = DATABASE_URI
        database.expose_in_sqllab = True
        database.allow_run_async = False
        if admin is not None:
            database.owners = [admin]
        db.session.flush()

        managed_datasets: dict[str, SqlaTable] = {}
        for definition in DATASETS:
            dataset = (
                db.session.query(SqlaTable)
                .filter(
                    SqlaTable.database_id == database.id,
                    SqlaTable.schema == "public",
                    SqlaTable.table_name == definition.table_name,
                )
                .one_or_none()
            )
            if dataset is None:
                dataset = SqlaTable(
                    database=database,
                    schema="public",
                    table_name=definition.table_name,
                )
                db.session.add(dataset)
            dataset.main_dttm_col = definition.main_dttm_col
            dataset.filter_select_enabled = True
            dataset.is_sqllab_view = False
            if admin is not None:
                dataset.owners = [admin]
            db.session.flush()
            dataset.fetch_metadata()
            available_columns = {column.column_name for column in dataset.columns}
            missing_columns = set(definition.columns) - available_columns
            if missing_columns:
                raise RuntimeError(
                    f"{definition.table_name} is missing configured columns: "
                    f"{sorted(missing_columns)}"
                )
            managed_datasets[definition.table_name] = dataset

        db.session.flush()
        charts: list[Slice] = []
        for definition in DATASETS:
            dataset = managed_datasets[definition.table_name]
            chart = (
                db.session.query(Slice)
                .filter(Slice.slice_name == definition.chart_name)
                .one_or_none()
            )
            if chart is None:
                chart = Slice(slice_name=definition.chart_name)
                db.session.add(chart)
            chart.viz_type = "table"
            chart.datasource_type = DatasourceType.TABLE
            chart.datasource_id = dataset.id
            chart.datasource_name = dataset.table_name
            chart.params = _chart_params(dataset.id, definition)
            if admin is not None:
                chart.owners = [admin]
            charts.append(chart)

        db.session.flush()
        dashboard = (
            db.session.query(Dashboard)
            .filter(Dashboard.slug == DASHBOARD_SLUG)
            .one_or_none()
        )
        if dashboard is None:
            dashboard = Dashboard(slug=DASHBOARD_SLUG)
            db.session.add(dashboard)
        dashboard.dashboard_title = DASHBOARD_TITLE
        dashboard.published = True
        if admin is not None:
            dashboard.owners = [admin]
        dashboard.slices = charts
        dashboard.position_json = _dashboard_position(charts)
        dashboard.json_metadata = json.dumps(
            {
                "color_scheme": "supersetColors",
                "expanded_slices": {},
                "native_filter_configuration": [],
                "refresh_frequency": 0,
                "timed_refresh_immune_slices": [],
            },
            sort_keys=True,
        )
        db.session.commit()

        print(
            "Provisioned Aegis Superset workspace: "
            f"{len(managed_datasets)} datasets, {len(charts)} charts, "
            f"dashboard /superset/dashboard/{DASHBOARD_SLUG}/"
        )


if __name__ == "__main__":
    provision()
