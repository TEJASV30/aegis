"""Strict UTC normalization helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd


def utc_timestamp(value: Any) -> pd.Timestamp:
    """Parse one timezone-aware timestamp and normalize it to UTC."""

    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp '{value}' must include a timezone offset.")
    return parsed.tz_convert("UTC")


def utc_series(values: Iterable[Any]) -> pd.Series:
    """Normalize a timestamp sequence to ``datetime64[ns, UTC]`` strictly."""

    normalized = [utc_timestamp(value) for value in values]
    return pd.Series(pd.DatetimeIndex(normalized), dtype="datetime64[ns, UTC]")
