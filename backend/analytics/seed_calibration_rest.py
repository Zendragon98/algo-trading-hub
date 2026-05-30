"""Seed symbol_calibration.json from live Binance bookTicker spreads (no L2 parquet).

CLI:
    python -m analytics.seed_calibration_rest --from-mm-symbols
    python -m analytics.seed_calibration_rest --symbols BTCUSDT,ETHUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from common.config import get_settings

from .symbol_calibrator import (
    SymbolCalibrationPayload,
    write_symbol_calibration,
)

logger = logging.getLogger(__name__)


def _half_spread_from_bid_ask(bid: float, ask: float) -> tuple[float, float]:
    mid = (bid + ask) / 2.0
    if mid <= 0 or ask <= bid:
        return 2.0, 1.0
    spread_bps = (ask - bid) / mid * 10_000.0
    half = max(0.8, min(50.0, spread_bps * 0.55 + 0.5))
    min_spread = max(0.5, spread_bps * 0.4)
    return half, min_spread


def _payload_from_spread(symbol: str, half: float, min_spread: float, settings) -> SymbolCalibrationPayload:
    return SymbolCalibrationPayload(
        symbol=symbol.upper(),
        samples=1,
        mm={
            "half_spread_bps": half,
            "min_spread_bps": min_spread,
            "reservation_inventory_bps": max(6.0, half * 6.0),
            "inventory_spread_skew_bps": max(3.0, half * 2.5),
            "toxic_widen_bps": max(4.0, half * 4.0),
            "depletion_widen_bps": max(3.0, half * 3.0),
            "skew_scale": 1.0,
            "imbalance_scale": 8.0,
            "tape_scale": float(settings.mm_tape_scale),
            "depletion_scale": float(settings.mm_depletion_scale),
            "reservation_micro_weight": float(settings.mm_reservation_micro_weight),
            "jump_return_bps": max(20.0, half * 18.0),
            "jump_vol_mult": float(settings.mm_jump_vol_mult),
            "max_adverse_markout_bps": max(8.0, half * 8.0),
            "scratch_loss_bps": max(12.0, half * 10.0),
            "min_exit_profit_bps": max(2.0, half * 1.5),
            "toxicity_threshold": float(settings.mm_toxicity_threshold),
            "depletion_pull_ratio": 0.25,
            "depletion_breaker_ratio": 0.2,
            "min_skew_bps": 1.0,
            "quote_size_pct": float(settings.mm_quote_size_pct),
            "venue_spread_mult": float(settings.mm_quote_venue_spread_mult),
        },
        execution={
            "imbalance_threshold": 0.2,
            "hit_ratio_threshold": 0.6,
            "spread_wide_floor_bps": max(2.0, half * 1.2),
            "spread_wide_ceiling_bps": max(80.0, half * 40.0),
        },
        risk={"max_entry_spread_bps": max(60.0, half * 50.0)},
        fees={
            "maker_fee_bps": float(settings.mm2_maker_fee_bps),
            "taker_fee_bps": float(settings.mm2_taker_fee_bps),
            "spread_buffer_bps": max(1.0, half * 0.8),
        },
    )


async def fetch_book_tickers(
    symbols: list[str],
    *,
    rest_base: str,
) -> dict[str, tuple[float, float]]:
    import aiohttp

    out: dict[str, tuple[float, float]] = {}
    url = f"{rest_base.rstrip('/')}/fapi/v1/ticker/bookTicker"
    async with aiohttp.ClientSession() as session:
        if len(symbols) <= 20:
            for sym in symbols:
                async with session.get(url, params={"symbol": sym}) as resp:
                    resp.raise_for_status()
                    row = await resp.json()
                    bid = float(row["bidPrice"])
                    ask = float(row["askPrice"])
                    out[sym.upper()] = (bid, ask)
            return out
        async with session.get(url) as resp:
            resp.raise_for_status()
            rows = await resp.json()
        want = {s.upper() for s in symbols}
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if sym in want:
                out[sym] = (float(row["bidPrice"]), float(row["askPrice"]))
    return out


async def run_seed(symbols: list[str]) -> Path:
    settings = get_settings()
    quotes = await fetch_book_tickers(symbols, rest_base=settings.binance_rest_base)
    payloads: dict[str, SymbolCalibrationPayload] = {}
    for sym in symbols:
        sym_u = sym.upper()
        pair = quotes.get(sym_u)
        if pair is None:
            logger.warning("%s: no bookTicker — skipped", sym_u)
            continue
        half, min_spread = _half_spread_from_bid_ask(pair[0], pair[1])
        payloads[sym_u] = _payload_from_spread(sym_u, half, min_spread, settings)
        logger.info("%s: half_spread_bps=%.2f min_spread_bps=%.2f", sym_u, half, min_spread)
    if not payloads:
        raise SystemExit("no symbols calibrated")
    return write_symbol_calibration(payloads)


def _resolve_symbols(args: argparse.Namespace, settings) -> list[str]:
    if args.from_mm_symbols or (len(args.symbols) == 1 and args.symbols[0].upper() == "AUTO"):
        raw = settings.mm2_symbols if settings.strategy == "market_making_v2" else settings.mm_symbols
        from common.universe_bootstrap import is_auto_symbol_list

        if is_auto_symbol_list(raw):
            from analytics.mm_universe_scanner import load_mm_universe_report

            report = load_mm_universe_report()
            if report and report.recommended:
                return list(report.recommended)
            from analytics.mm_universe_scanner import resolve_mm_universe

            return asyncio.run(resolve_mm_universe(settings, force_rescan=False))
        return [s.strip().upper() for s in raw if s.strip()]
    return [s.strip().upper() for s in args.symbols if s.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed calibration from REST bookTicker")
    parser.add_argument("--symbols", nargs="+", default=["AUTO"])
    parser.add_argument("--from-mm-symbols", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    syms = _resolve_symbols(args, settings)
    if not syms:
        raise SystemExit("no symbols")
    path = asyncio.run(run_seed(syms))
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
