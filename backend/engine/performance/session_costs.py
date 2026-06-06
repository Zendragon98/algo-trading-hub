"""Convert venue commission / funding events to USD for session KPI rollups."""

from __future__ import annotations

_STABLE_USD_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD"})


def stable_usd_amount(amount: float, asset: str) -> float:
    """Return ``amount`` when ``asset`` is a USD stablecoin, else 0."""
    if amount <= 0.0:
        return 0.0
    if (asset or "USDT").upper() in _STABLE_USD_ASSETS:
        return float(amount)
    return 0.0


def commission_to_usd(fee: float, fee_asset: str) -> float:
    """Map Binance ``ORDER_TRADE_UPDATE`` commission to a USD stable amount."""
    return stable_usd_amount(fee, fee_asset)
