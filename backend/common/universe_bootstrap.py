"""Resolve SYMBOLS / SMA / BLEND / MM AUTO universes at process boot (Binance only)."""

from __future__ import annotations

import logging
from typing import Any

from analytics.mm_universe_scanner import resolve_mm_universe
from common.config import Settings
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_perps, discover_usdt_usdc_pairs

logger = logging.getLogger(__name__)

_AUTO = "AUTO"


def is_auto_symbol_list(symbols: list[str] | None) -> bool:
    """True when the list is empty or the single token ``AUTO``."""
    requested = [s.strip().upper() for s in (symbols or []) if s.strip()]
    return (not requested) or (len(requested) == 1 and requested[0] == _AUTO)


def needs_auto_universe_resolve(settings: Settings) -> bool:
    """Whether any configured symbol list still requests AUTO expansion."""
    if settings.venue != "binance":
        return False
    return (
        is_auto_symbol_list(settings.symbols)
        or is_auto_symbol_list(settings.sma_symbols)
        or is_auto_symbol_list(settings.blend_symbols)
        or is_auto_symbol_list(settings.mm_symbols)
        or is_auto_symbol_list(settings.mm2_symbols)
    )


def _filter_and_cap_usdt_perps(
    universe: list[str],
    stats: dict[str, tuple[float, float]],
    *,
    max_symbols: int,
    min_mid_price: float,
    label: str,
) -> list[str]:
    if min_mid_price > 0:
        before = len(universe)
        universe = [s for s in universe if stats.get(s, (0.0, 0.0))[1] >= min_mid_price]
        dropped = before - len(universe)
        if dropped:
            logger.info(
                "%s filtered %d symbols below min mid %.4f",
                label,
                dropped,
                min_mid_price,
            )
    cap = int(max_symbols)
    if cap > 0 and len(universe) > cap:
        universe = sorted(
            universe,
            key=lambda s: stats.get(s, (0.0, 0.0))[0],
            reverse=True,
        )[:cap]
        logger.info("%s capped to top %d by 24h volume", label, cap)
    return universe


async def discover_capped_usdt_perps(
    rest: BinanceRestClient,
    exchange_info: dict[str, Any],
    *,
    max_symbols: int,
    min_mid_price: float,
    label: str,
) -> list[str]:
    """Top USDT perpetuals by 24h quote volume with optional mid-price filter."""
    universe = discover_usdt_perps(exchange_info)
    stats = await rest.fetch_24h_stats(universe)
    return _filter_and_cap_usdt_perps(
        universe,
        stats,
        max_symbols=max_symbols,
        min_mid_price=min_mid_price,
        label=label,
    )


async def resolve_binance_auto_universe(settings: Settings) -> Settings:
    """Expand AUTO symbol lists via REST before engine construction."""
    if settings.venue != "binance":
        return settings

    symbols_auto = is_auto_symbol_list(settings.symbols)
    sma_auto = is_auto_symbol_list(settings.sma_symbols)
    blend_auto = is_auto_symbol_list(settings.blend_symbols)
    mm_auto = is_auto_symbol_list(settings.mm_symbols)
    mm2_auto = is_auto_symbol_list(settings.mm2_symbols)

    if not (symbols_auto or sma_auto or blend_auto or mm_auto or mm2_auto):
        return settings

    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    try:
        info = await rest.exchange_info()
        updates: dict[str, list[str] | bool] = {}
        if symbols_auto:
            discovered = discover_usdt_usdc_pairs(info)
            updates["symbols"] = discovered
            bases = sorted({s.replace("USDT", "").replace("USDC", "") for s in discovered})
            logger.info(
                "SYMBOLS=AUTO -> %d symbols across %d bases: %s",
                len(discovered),
                len(bases),
                ", ".join(bases) if len(bases) <= 20 else f"{', '.join(bases[:20])}, ...",
            )
        if sma_auto:
            sma_universe = await discover_capped_usdt_perps(
                rest,
                info,
                max_symbols=int(settings.sma_max_symbols),
                min_mid_price=float(settings.sma_min_mid_price),
                label="SMA_SYMBOLS",
            )
            updates["sma_symbols"] = sma_universe
            logger.info("SMA_SYMBOLS=AUTO -> %d USDT perpetuals", len(sma_universe))
        if blend_auto:
            blend_universe = await discover_capped_usdt_perps(
                rest,
                info,
                max_symbols=int(settings.blend_max_symbols),
                min_mid_price=float(settings.blend_min_mid_price),
                label="BLEND_SYMBOLS",
            )
            updates["blend_symbols"] = blend_universe
            logger.info("BLEND_SYMBOLS=AUTO -> %d USDT perpetuals", len(blend_universe))
        if mm_auto or mm2_auto:
            mm_universe = await resolve_mm_universe(settings, rest=rest)
            if mm_auto:
                updates["mm_symbols"] = mm_universe
                updates["mm_universe_auto"] = True
            if mm2_auto:
                updates["mm2_symbols"] = mm_universe
                updates["mm2_universe_auto"] = True
            logger.info(
                "MM_SYMBOLS=AUTO -> %d symbols: %s",
                len(mm_universe),
                ", ".join(mm_universe[:12]) + (" ..." if len(mm_universe) > 12 else ""),
            )
        if updates:
            return settings.model_copy(update=updates)
        return settings
    finally:
        await rest.close()
