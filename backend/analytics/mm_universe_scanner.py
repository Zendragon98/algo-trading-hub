"""Scan Binance USDT perps for market-making suitability (liquidity, spread, stability).

Writes ``data/mm_universe_scan.json`` with ranked symbols and recommended universe.
Used when ``MM_SYMBOLS=AUTO`` / ``MM2_SYMBOLS=AUTO`` at engine boot, or via CLI:

    python -m analytics.mm_universe_scanner
    python -m analytics.mm_universe_scanner --max-symbols 8 --no-sample
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from common.config import Settings, get_settings
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_perps

from .kline_store import backend_data_root

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = backend_data_root() / "mm_universe_scan.json"


@dataclass(slots=True)
class MmSymbolScore:
    symbol: str
    quote_volume_24h: float
    last_price: float
    median_spread_bps: float
    spread_cv: float
    mid_vol_bps: float
    edge_bps: float
    score: float
    eligible: bool
    reject_reason: str | None = None


@dataclass(slots=True)
class MmUniverseReport:
    generated_at: str
    recommended: list[str]
    rankings: list[MmSymbolScore]
    candidates_scanned: int
    sample_rounds: int


def _min_edge_bps(settings: Settings) -> float:
    explicit = float(settings.mm_auto_min_edge_bps)
    if explicit > 0:
        return explicit
    maker = float(settings.mm2_maker_fee_bps)
    buffer = float(settings.mm2_spread_buffer_bps)
    return 2.0 * maker + buffer


def score_mm_candidate(
    *,
    quote_volume: float,
    median_spread_bps: float,
    spread_cv: float,
    mid_vol_bps: float,
    min_volume: float,
    min_spread_bps: float,
    max_spread_bps: float,
    max_spread_cv: float,
    max_mid_vol_bps: float,
    min_edge_bps: float,
) -> tuple[float, bool, str | None]:
    """Return (composite_score, eligible, reject_reason)."""
    if quote_volume < min_volume:
        return 0.0, False, "low_volume"
    if median_spread_bps < min_spread_bps:
        return 0.0, False, "spread_too_tight"
    if median_spread_bps > max_spread_bps:
        return 0.0, False, "spread_too_wide"
    if spread_cv > max_spread_cv:
        return 0.0, False, "unstable_spread"
    if mid_vol_bps > max_mid_vol_bps:
        return 0.0, False, "mid_too_volatile"

    edge_bps = median_spread_bps - min_edge_bps
    if edge_bps < 0:
        return 0.0, False, "insufficient_edge"

    vol_score = min(1.0, math.log10(max(quote_volume, 1.0)) / 9.0)
    edge_score = min(1.0, edge_bps / max(min_edge_bps * 2.0, 1.0))
    stability = 1.0 / (1.0 + spread_cv * 4.0)
    calm = 1.0 / (1.0 + mid_vol_bps / max(max_mid_vol_bps, 1.0))
    composite = 0.35 * vol_score + 0.30 * edge_score + 0.20 * stability + 0.15 * calm
    return composite * 100.0, True, None


def _spread_from_book_row(row: dict[str, Any]) -> float | None:
    try:
        bid = float(row["bidPrice"])
        ask = float(row["askPrice"])
    except (KeyError, TypeError, ValueError):
        return None
    if bid <= 0 or ask <= bid:
        return None
    return (ask - bid) / bid * 10_000.0


def _mid_from_book_row(row: dict[str, Any]) -> float | None:
    try:
        bid = float(row["bidPrice"])
        ask = float(row["askPrice"])
    except (KeyError, TypeError, ValueError):
        return None
    if bid <= 0 or ask <= bid:
        return None
    return (bid + ask) / 2.0


async def _sample_spreads(
    rest: BinanceRestClient,
    symbols: list[str],
    *,
    rounds: int,
    interval_sec: float,
) -> dict[str, tuple[float, float, float]]:
    """Return per symbol (median_spread_bps, spread_cv, mid_vol_bps)."""
    spread_hist: dict[str, list[float]] = {s: [] for s in symbols}
    mid_hist: dict[str, list[float]] = {s: [] for s in symbols}
    sym_set = set(symbols)

    for _ in range(max(1, rounds)):
        rows = await rest.book_ticker()
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if sym not in sym_set:
                continue
            sp = _spread_from_book_row(row)
            mid = _mid_from_book_row(row)
            if sp is not None:
                spread_hist[sym].append(sp)
            if mid is not None:
                mid_hist[sym].append(mid)
        if interval_sec > 0 and rounds > 1:
            await asyncio.sleep(interval_sec)

    out: dict[str, tuple[float, float, float]] = {}
    for sym in symbols:
        spreads = spread_hist.get(sym) or []
        mids = mid_hist.get(sym) or []
        if len(spreads) < 3:
            continue
        sp_arr = np.asarray(spreads, dtype=float)
        median_sp = float(np.median(sp_arr))
        mean_sp = float(np.mean(sp_arr))
        spread_cv = float(np.std(sp_arr) / mean_sp) if mean_sp > 0 else 999.0
        mid_vol = 0.0
        if len(mids) >= 4:
            m_arr = np.asarray(mids, dtype=float)
            rets = np.diff(np.log(m_arr))
            mid_vol = float(np.std(rets) * 10_000.0) if len(rets) else 0.0
        out[sym] = (median_sp, spread_cv, mid_vol)
    return out


async def scan_mm_universe(
    settings: Settings | None = None,
    *,
    rest: BinanceRestClient | None = None,
    sample: bool = True,
) -> MmUniverseReport:
    """Rank USDT perps; return report with ``recommended`` top-N symbols."""
    settings = settings or get_settings()
    own_rest = rest is None
    client = rest or BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    try:
        info = await client.exchange_info()
        universe = discover_usdt_perps(info)
        stats = await client.fetch_24h_stats(universe)

        min_px = float(settings.mm_auto_min_mid_price)
        min_vol = float(settings.mm_auto_min_quote_volume)
        prefilter = int(settings.mm_auto_prefilter_top_volume)

        candidates: list[tuple[str, float, float]] = []
        for sym in universe:
            row = stats.get(sym)
            if row is None:
                continue
            qv, px = row
            if px < min_px or qv < min_vol:
                continue
            candidates.append((sym, qv, px))
        candidates.sort(key=lambda x: x[1], reverse=True)
        if prefilter > 0:
            candidates = candidates[:prefilter]

        sym_list = [c[0] for c in candidates]
        sample_stats: dict[str, tuple[float, float, float]] = {}
        if sample and sym_list:
            sample_stats = await _sample_spreads(
                client,
                sym_list,
                rounds=int(settings.mm_auto_sample_rounds),
                interval_sec=float(settings.mm_auto_sample_interval_sec),
            )
        else:
            rows = await client.book_ticker()
            by_sym = {str(r.get("symbol", "")).upper(): r for r in rows}
            for sym in sym_list:
                row = by_sym.get(sym)
                if row is None:
                    continue
                sp = _spread_from_book_row(row)
                if sp is not None:
                    sample_stats[sym] = (sp, 0.0, 0.0)

        min_edge = _min_edge_bps(settings)
        rankings: list[MmSymbolScore] = []
        for sym, qv, px in candidates:
            sampled = sample_stats.get(sym)
            if sampled is None:
                rankings.append(
                    MmSymbolScore(
                        symbol=sym,
                        quote_volume_24h=qv,
                        last_price=px,
                        median_spread_bps=0.0,
                        spread_cv=999.0,
                        mid_vol_bps=999.0,
                        edge_bps=0.0,
                        score=0.0,
                        eligible=False,
                        reject_reason="no_spread_samples",
                    ),
                )
                continue
            median_sp, spread_cv, mid_vol = sampled
            edge = median_sp - min_edge
            score, eligible, reason = score_mm_candidate(
                quote_volume=qv,
                median_spread_bps=median_sp,
                spread_cv=spread_cv,
                mid_vol_bps=mid_vol,
                min_volume=min_vol,
                min_spread_bps=float(settings.mm_auto_min_spread_bps),
                max_spread_bps=float(settings.mm_auto_max_spread_bps),
                max_spread_cv=float(settings.mm_auto_max_spread_cv),
                max_mid_vol_bps=float(settings.mm_auto_max_mid_vol_bps),
                min_edge_bps=min_edge,
            )
            rankings.append(
                MmSymbolScore(
                    symbol=sym,
                    quote_volume_24h=qv,
                    last_price=px,
                    median_spread_bps=median_sp,
                    spread_cv=spread_cv,
                    mid_vol_bps=mid_vol,
                    edge_bps=edge,
                    score=score,
                    eligible=eligible,
                    reject_reason=reason,
                ),
            )

        eligible = [r for r in rankings if r.eligible]
        eligible.sort(key=lambda r: r.score, reverse=True)
        cap = int(settings.mm_auto_max_symbols)
        recommended = [r.symbol for r in eligible[:cap]] if cap > 0 else [r.symbol for r in eligible]
        if not recommended:
            fallback = ["BTCUSDT", "ETHUSDT"]
            logger.warning(
                "mm universe scan: no eligible symbols; falling back to %s",
                fallback,
            )
            recommended = fallback

        return MmUniverseReport(
            generated_at=datetime.now(UTC).isoformat(),
            recommended=recommended,
            rankings=rankings,
            candidates_scanned=len(candidates),
            sample_rounds=int(settings.mm_auto_sample_rounds) if sample else 0,
        )
    finally:
        if own_rest:
            await client.close()


def write_mm_universe_report(
    report: MmUniverseReport,
    path: Path | None = None,
) -> Path:
    dest = path or DEFAULT_REPORT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "generated_at": report.generated_at,
        "recommended": report.recommended,
        "candidates_scanned": report.candidates_scanned,
        "sample_rounds": report.sample_rounds,
        "rankings": [asdict(r) for r in report.rankings],
    }
    dest.write_text(json.dumps(body, indent=2), encoding="utf-8")
    logger.info(
        "mm universe scan: %d candidates, %d eligible, recommended=%s -> %s",
        report.candidates_scanned,
        sum(1 for r in report.rankings if r.eligible),
        report.recommended,
        dest,
    )
    return dest


def load_mm_universe_report(path: Path | None = None) -> MmUniverseReport | None:
    dest = path or DEFAULT_REPORT_PATH
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        rankings = [MmSymbolScore(**row) for row in data.get("rankings", [])]
        return MmUniverseReport(
            generated_at=str(data.get("generated_at", "")),
            recommended=[str(s).upper() for s in data.get("recommended", [])],
            rankings=rankings,
            candidates_scanned=int(data.get("candidates_scanned", 0)),
            sample_rounds=int(data.get("sample_rounds", 0)),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("failed to load mm universe report %s", dest)
        return None


def report_is_fresh(report: MmUniverseReport, ttl_sec: float) -> bool:
    if ttl_sec <= 0 or not report.generated_at:
        return False
    try:
        ts = datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
        age = time.time() - ts.timestamp()
        return age <= ttl_sec
    except ValueError:
        return False


async def resolve_mm_universe(
    settings: Settings | None = None,
    *,
    rest: BinanceRestClient | None = None,
    force_rescan: bool = False,
) -> list[str]:
    """Return recommended MM symbols (scan or cached report)."""
    settings = settings or get_settings()
    ttl = float(settings.mm_auto_scan_ttl_sec)
    if not force_rescan:
        cached = load_mm_universe_report()
        if cached is not None and report_is_fresh(cached, ttl) and cached.recommended:
            logger.info(
                "mm universe: using cached report (%s, age ok)",
                cached.recommended,
            )
            return list(cached.recommended)

    report = await scan_mm_universe(settings, rest=rest)
    write_mm_universe_report(report)
    return list(report.recommended)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for MM-suitable USDT perps")
    parser.add_argument("--max-symbols", type=int, default=0, help="Override MM_AUTO_MAX_SYMBOLS")
    parser.add_argument("--no-sample", action="store_true", help="Single bookTicker snapshot only")
    parser.add_argument("--force", action="store_true", help="Ignore cached report")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    overrides: dict[str, object] = {}
    if args.max_symbols > 0:
        overrides["mm_auto_max_symbols"] = args.max_symbols
    if overrides:
        settings = settings.model_copy(update=overrides)

    async def _run() -> None:
        if args.force or args.no_sample:
            report = await scan_mm_universe(settings, sample=not args.no_sample)
            write_mm_universe_report(report, Path(args.output))
            syms = report.recommended
        else:
            syms = await resolve_mm_universe(settings, force_rescan=False)
        print(json.dumps({"recommended": syms}, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    main()
