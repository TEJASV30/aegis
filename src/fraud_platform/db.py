"""PostgreSQL connection helpers used by batch jobs and serving."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fraud_platform.config import get_settings

_POOL: ConnectionPool[Connection[dict[str, Any]]] | None = None


def get_pool() -> ConnectionPool[Connection[dict[str, Any]]]:
    """Create the process-local connection pool on first use."""

    global _POOL
    if _POOL is None:
        settings = get_settings()
        _POOL = ConnectionPool(
            conninfo=settings.postgres_dsn,
            min_size=1,
            max_size=10,
            timeout=settings.db_pool_timeout_seconds,
            max_waiting=50,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": settings.db_connect_timeout_seconds,
                "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            },
            open=True,
        )
    return _POOL


@contextmanager
def database_connection() -> Generator[Connection[dict[str, Any]], None, None]:
    """Yield a pooled PostgreSQL connection with automatic transaction handling."""

    with get_pool().connection() as connection:
        with connection.transaction():
            yield connection


def ping_database() -> bool:
    """Return whether PostgreSQL accepts a bounded probe query."""

    with database_connection() as connection:
        row = connection.execute("SELECT 1 AS ready").fetchone()
    return bool(row and row["ready"] == 1)
