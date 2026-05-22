"""Resolve SYMBOLS / SMA / MM AUTO universes at process boot (Binance only)."""

from __future__ import annotations

import logging

from analytics.mm_universe_scanner import resolve_mm_universe
from common.config import Settings
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_perps, discover_usdt_usdc_pairs

logger = logging.getLogger(__name__)


async def resolve_binance_auto_universe(settings: Settings) -> Settings:
    """Expand AUTO symbol lists via REST before engine construction."""
    if settings.venue != "binance":
        return settings

    requested = [s.strip().upper() for s in settings.symbols]
    symbols_auto = (not requested) or (len(requested) == 1 and requested[0] == "AUTO")
    sma_requested = [s.strip().upper() for s in settings.sma_symbols] if settings.sma_symbols else []
    sma_auto = not sma_requested or (len(sma_requested) == 1 and sma_requested[0] == "AUTO")
    mm_requested = [s.strip().upper() for s in (settings.mm_symbols or []) if s.strip()]
    mm2_requested = [s.strip().upper() for s in (settings.mm2_symbols or []) if s.strip()]
    mm_auto = (not mm_requested) or (len(mm_requested) == 1 and mm_requested[0] == "AUTO")
    mm2_auto = (not mm2_requested) or (len(mm2_requested) == 1 and mm2_requested[0] == "AUTO")

    if not (symbols_auto or sma_auto or mm_auto or mm2_auto):
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
            sma_universe = discover_usdt_perps(info)
            stats = await rest.fetch_24h_stats(sma_universe)
            min_px = float(settings.sma_min_mid_price)
            if min_px > 0:
                before = len(sma_universe)
                sma_universe = [
                    s for s in sma_universe if stats.get(s, (0.0, 0.0))[1] >= min_px
                ]
                dropped = before - len(sma_universe)
                if dropped:
                    logger.info(
                        "SMA_SYMBOLS filtered %d symbols below min mid %.4f",
                        dropped,
                        min_px,
                    )
            cap = int(settings.sma_max_symbols)
            if cap > 0 and len(sma_universe) > cap:
                sma_universe = sorted(
                    sma_universe,
                    key=lambda s: stats.get(s, (0.0, 0.0))[0],
                    reverse=True,
                )[:cap]
                logger.info("SMA_SYMBOLS capped to top %d by 24h volume", cap)
            updates["sma_symbols"] = sma_universe
            logger.info("SMA_SYMBOLS=AUTO -> %d USDT perpetuals", len(sma_universe))
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
