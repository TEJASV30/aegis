"""Deterministic currency normalization shared by batch and online features.

The rates are an explicit synthetic-data contract, not live foreign-exchange rates.
Production deployments should replace this table with an effective-dated rate table.
"""

from __future__ import annotations

from collections.abc import Mapping

CURRENCY_RATE_VERSION = "synthetic-fx-2025-01-v2"
USD_PER_UNIT: Mapping[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "INR": 0.012,
    "JPY": 0.0067,
    "SGD": 0.74,
}


def normalize_currency(currency: str) -> str:
    """Return a supported uppercase ISO currency code."""

    normalized = currency.strip().upper()
    if normalized not in USD_PER_UNIT:
        supported = ", ".join(sorted(USD_PER_UNIT))
        raise ValueError(f"Unsupported currency '{currency}'; expected one of {supported}.")
    return normalized


def amount_to_usd(amount: float, currency: str) -> float:
    """Convert a monetary amount to the versioned synthetic USD basis."""

    if amount < 0:
        raise ValueError("Amount cannot be negative.")
    normalized = normalize_currency(currency)
    return float(amount) * USD_PER_UNIT[normalized]


def amount_from_usd(amount_usd: float, currency: str) -> float:
    """Convert the synthetic USD basis into a local-currency amount."""

    if amount_usd < 0:
        raise ValueError("Amount cannot be negative.")
    normalized = normalize_currency(currency)
    return float(amount_usd) / USD_PER_UNIT[normalized]
