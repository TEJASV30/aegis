"""Shared test configuration."""

from __future__ import annotations

import os

import pytest


def postgres_available() -> bool:
    """Return whether integration tests were given an explicit database."""

    return bool(os.getenv("POSTGRES_DSN"))


@pytest.fixture
def require_postgres() -> None:
    if not postgres_available():
        pytest.skip("POSTGRES_DSN is required for PostgreSQL integration tests")
