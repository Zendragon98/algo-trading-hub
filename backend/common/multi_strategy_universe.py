"""Partition STRATEGY=all symbol lists into non-overlapping tiers at boot."""

from __future__ import annotations

import logging

from analytics.mm_universe_scanner import load_mm_universe_report
from common.config import Settings
from common.universe_bootstrap import is_auto_symbol_list

logger = logging.getLogger(__name__)

_DEFAULT_CANDIDATES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def _ordered_candidates(settings: Settings) -> list[str]:
    cached = load_mm_universe_report()
    if cached is not None and cached.recommended:
        raw = list(cached.recommended)
    else:
        pins = [s.strip().upper() for s in (settings.mm_auto_pin_symbols or []) if s.strip()]
        raw = pins or list(_DEFAULT_CANDIDATES)

    seen: set[str] = set()
    ordered: list[str] = []
    for sym in raw:
        su = sym.strip().upper()
        if su and su not in seen:
            seen.add(su)
            ordered.append(su)
    return ordered


def _pair_symbols_for_all(settings: Settings) -> list[str]:
    bases = [
        b.strip().upper()
        for b in (settings.multi_strategy_pair_bases or [])
        if b.strip()
    ]
    if not bases:
        bases = ["BTC", "ETH", "SOL"]
    symbols: list[str] = []
    for base in bases:
        symbols.append(f"{base}USDT")
        symbols.append(f"{base}USDC")
    return symbols


def _validate_pair_legs(symbols: list[str]) -> None:
    usdt = {s.removesuffix("USDT") for s in symbols if s.endswith("USDT")}
    usdc = {s.removesuffix("USDC") for s in symbols if s.endswith("USDC")}
    missing_usdc = sorted(usdt - usdc)
    missing_usdt = sorted(usdc - usdt)
    if missing_usdc:
        logger.warning(
            "STRATEGY=all pairs: USDT legs without USDC partner: %s",
            ", ".join(missing_usdc),
        )
    if missing_usdt:
        logger.warning(
            "STRATEGY=all pairs: USDC legs without USDT partner: %s",
            ", ".join(missing_usdt),
        )


def partition_multi_strategy_universe(settings: Settings) -> Settings:
    """Assign disjoint mm2 / sma / blend / flow universes when strategy=all."""
    if (settings.strategy or "").strip().lower() != "all":
        return settings
    if not bool(settings.multi_strategy_partition):
        return settings

    candidates = _ordered_candidates(settings)
    mm_n = max(0, int(settings.mm_auto_max_symbols))
    sma_n = max(0, int(settings.sma_max_symbols))
    blend_n = max(0, int(settings.blend_max_symbols))
    flow_n = max(0, int(settings.flow_max_symbols))

    mm2 = candidates[:mm_n] if mm_n else []
    rest = candidates[mm_n:]
    sma = rest[:sma_n] if sma_n else []
    rest = rest[sma_n:]
    blend = rest[:blend_n] if blend_n else []
    rest = rest[blend_n:]
    flow = rest[:flow_n] if flow_n > 0 else rest

    updates: dict[str, object] = {}
    if is_auto_symbol_list(settings.mm2_symbols) and mm2:
        updates["mm2_symbols"] = mm2
        updates["mm2_universe_auto"] = True
    if is_auto_symbol_list(settings.sma_symbols) and sma:
        updates["sma_symbols"] = sma
    if is_auto_symbol_list(settings.blend_symbols) and blend:
        updates["blend_symbols"] = blend
    if is_auto_symbol_list(settings.flow_symbols) and flow:
        updates["flow_symbols"] = flow
        updates["flow_universe_auto"] = True

    pair_syms = _pair_symbols_for_all(settings)
    if is_auto_symbol_list(settings.symbols) or (settings.strategy or "").lower() == "all":
        updates["symbols"] = pair_syms
        _validate_pair_legs(pair_syms)

    if not updates:
        return settings

    logger.info(
        "STRATEGY=all partition: mm2=%d sma=%d blend=%d flow=%d pairs=%d bases "
        "(overlap among single-leg tiers = 0 by construction)",
        len(mm2),
        len(sma),
        len(blend),
        len(flow),
        len(pair_syms) // 2,
    )
    for label, tier in (
        ("mm2", mm2),
        ("sma", sma),
        ("blend", blend),
        ("flow", flow),
    ):
        if tier:
            preview = ", ".join(tier[:8]) + (" ..." if len(tier) > 8 else "")
            logger.info("  %s -> [%s]", label, preview)

    return settings.model_copy(update=updates)
