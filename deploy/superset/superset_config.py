"""Minimal local Superset configuration; secrets must change outside development."""

import os

SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "change-this-local-secret")
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SUPERSET_METADATA_DSN",
    "postgresql+psycopg2://fraud:fraud@postgres:5432/superset",
)
WTF_CSRF_ENABLED = True
TALISMAN_ENABLED = False
FEATURE_FLAGS = {"DASHBOARD_RBAC": True}
