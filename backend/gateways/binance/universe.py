"""Helpers to discover tradable symbol universes from Binance exchangeInfo.

The live engine can be configured with SYMBOLS=AUTO to pull the available
USDT/USDC perp pairs from the venue at startup (instead of hardcoding a
coin list in .env).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def discover_usdt_usdc_pairs(exchange_info: dict[str, Any]) -> list[str]:
    """Return a flat symbols list containing matched (*USDT, *USDC) perp legs.

    Filters `exchangeInfo["symbols"]` down to contracts that are:
    - status == "TRADING"
    - contractType == "PERPETUAL" (if present)
    - quoteAsset in {"USDT", "USDC"}

    Then returns the union of both legs for every baseAsset that has both
    a USDT and USDC quoted contract.
    """

    symbols = exchange_info.get("symbols") or []
    legs: dict[str, dict[str, str]] = defaultdict(dict)  # base -> quote -> symbol

    for item in symbols:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "TRADING":
            continue
        contract_type = item.get("contractType")
        if contract_type is not None and contract_type != "PERPETUAL":
            continue

        quote = item.get("quoteAsset")
        if quote not in {"USDT", "USDC"}:
            continue
        base = item.get("baseAsset")
        sym = item.get("symbol")
        if not base or not sym:
            continue

        # Some listings can exist but be paused; keep only TRADING above.
        legs[str(base)][str(quote)] = str(sym).upper()

    out: list[str] = []
    for base, quotes in legs.items():
        usdt = quotes.get("USDT")
        usdc = quotes.get("USDC")
        if not usdt or not usdc:
            continue
        out.extend([usdt, usdc])

    # Stable, deterministic order for logs / tests.
    return sorted(set(out))


def discover_usdt_perps(exchange_info: dict[str, Any]) -> list[str]:
    """Return every TRADING USDT-quoted perpetual contract.

    Unlike ``discover_usdt_usdc_pairs`` this does not require a USDC twin,
    so the SMA scanner can operate over the venue's full liquid USDT
    universe (~545 symbols on Binance Futures mainnet, ~30+ on testnet).
    """
    symbols = exchange_info.get("symbols") or []
    out: list[str] = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "TRADING":
            continue
        contract_type = item.get("contractType")
        if contract_type is not None and contract_type != "PERPETUAL":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        sym = item.get("symbol")
        if not sym:
            continue
        out.append(str(sym).upper())
    return sorted(set(out))

