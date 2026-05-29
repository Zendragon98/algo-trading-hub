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
class TickerVolStats:
    quote_volume: float
    last_price: float
    high: float
    low: float
    price_change_pct: float
    range_vol_24h_bps: float


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
    range_vol_24h_bps: float = 0.0
    intraday_vol_bps: float = 0.0


@dataclass(slots=True)
class StabilityThresholds:
    max_spread_cv: float
    max_mid_vol_bps: float
    stability_percentile: float
    spread_cv_median: float
    mid_vol_median: float
    range_vol_24h_median: float
    source: str  # "percentile" | "override"


@dataclass(slots=True)
class MmUniverseReport:
    generated_at: str
    recommended: list[str]
    rankings: list[MmSymbolScore]
    candidates_scanned: int
    sample_rounds: int
    thresholds: StabilityThresholds | None = None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _range_vol_24h_bps(high: float, low: float, last: float) -> float:
    if last <= 0 or high < low:
        return 0.0
    return (high - low) / last * 10_000.0


def _sample_window_sec(settings: Settings) -> float:
    rounds = max(1, int(settings.mm_auto_sample_rounds))
    interval = float(settings.mm_auto_sample_interval_sec)
    return max(1.0, rounds * interval)


def _intraday_vol_from_range(range_vol_24h_bps: float, sample_window_sec: float) -> float:
    """Scale 24h high-low range to the bookTicker sample window (√time)."""
    if range_vol_24h_bps <= 0:
        return 0.0
    fraction = sample_window_sec / 86_400.0
    return range_vol_24h_bps * math.sqrt(fraction)


def _parse_ticker_vol_row(row: dict[str, Any]) -> TickerVolStats | None:
    try:
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            return None
        qv = float(row["quoteVolume"])
        last = float(row["lastPrice"])
        high = float(row["highPrice"])
        low = float(row["lowPrice"])
        chg = abs(float(row.get("priceChangePercent", 0.0)))
    except (KeyError, TypeError, ValueError):
        return None
    if last <= 0:
        return None
    return TickerVolStats(
        quote_volume=qv,
        last_price=last,
        high=high,
        low=low,
        price_change_pct=chg,
        range_vol_24h_bps=_range_vol_24h_bps(high, low, last),
    )


def _effective_mid_vol_bps(
    sampled_mid_vol: float,
    range_vol_24h_bps: float,
    *,
    sample_window_sec: float,
) -> tuple[float, float]:
    """Return (effective_mid_vol_bps, intraday_vol_bps from 24h range)."""
    intraday = _intraday_vol_from_range(range_vol_24h_bps, sample_window_sec)
    if sampled_mid_vol <= 0:
        return intraday, intraday
    if intraday <= 0:
        return sampled_mid_vol, 0.0
    return max(sampled_mid_vol, intraday * 0.9), intraday


def derive_stability_thresholds(
    settings: Settings,
    *,
    spread_cvs: list[float],
    mid_vols: list[float],
    range_vols_24h: list[float],
    intraday_vols: list[float],
) -> StabilityThresholds:
    """Derive max spread CV and mid-vol caps from the candidate cross-section."""
    pct = float(settings.mm_auto_stability_percentile)
    cv_floor = float(settings.mm_auto_spread_cv_floor)
    cv_cap = float(settings.mm_auto_spread_cv_cap)
    mid_floor = float(settings.mm_auto_mid_vol_floor_bps)
    mid_cap = float(settings.mm_auto_mid_vol_cap_bps)
    regime_mult = float(settings.mm_auto_vol_regime_mult)
    explicit_cv = float(settings.mm_auto_max_spread_cv)
    explicit_mid = float(settings.mm_auto_max_mid_vol_bps)

    clean_cvs = [c for c in spread_cvs if 0 < c < 5.0]
    clean_mids = [m for m in mid_vols if 0 <= m < 200.0]
    clean_ranges = [r for r in range_vols_24h if 0 < r < 800.0]
    clean_intraday = [v for v in intraday_vols if 0 < v < 200.0]
    vol_pool = clean_mids + clean_intraday

    cv_median = float(np.median(clean_cvs)) if clean_cvs else 0.0
    mid_median = float(np.median(vol_pool)) if vol_pool else 0.0
    range_median = float(np.median(clean_ranges)) if clean_ranges else 0.0

    if explicit_cv > 0:
        max_cv = explicit_cv
        source = "override"
    elif len(clean_cvs) >= 5:
        max_cv = float(np.percentile(clean_cvs, pct))
        source = "percentile"
    elif clean_cvs:
        max_cv = cv_median * 1.35
        source = "percentile"
    else:
        max_cv = 0.45
        source = "fallback"

    if explicit_mid > 0:
        max_mid = explicit_mid
        if source == "percentile":
            source = "override"
    elif len(vol_pool) >= 5:
        max_mid = float(np.percentile(vol_pool, pct))
        source = "percentile"
    elif vol_pool:
        max_mid = mid_median * 1.35
        source = "percentile"
    else:
        max_mid = 12.0
        source = "fallback"

    if clean_ranges and explicit_mid <= 0:
        regime = float(np.median(sorted(clean_ranges)[: min(5, len(clean_ranges))]))
        window = _sample_window_sec(settings)
        regime_short = _intraday_vol_from_range(regime, window)
        max_mid = max(max_mid, regime_short * regime_mult)

    max_cv = _clamp(max_cv, cv_floor, cv_cap)
    max_mid = _clamp(max_mid, mid_floor, mid_cap)

    return StabilityThresholds(
        max_spread_cv=max_cv,
        max_mid_vol_bps=max_mid,
        stability_percentile=pct,
        spread_cv_median=cv_median,
        mid_vol_median=mid_median,
        range_vol_24h_median=range_median,
        source=source,
    )


def _min_edge_bps(settings: Settings) -> float:
    explicit = float(settings.mm_auto_min_edge_bps)
    if explicit > 0:
        return explicit
    from engine.strategies.mm_calibrated import mm2_fee_edge_floor_bps

    return mm2_fee_edge_floor_bps("BTCUSDT", settings)


def _pin_symbols(settings: Settings) -> list[str]:
    return [s.strip().upper() for s in (settings.mm_auto_pin_symbols or []) if s.strip()]


def _ranking_by_symbol(rankings: list[MmSymbolScore]) -> dict[str, MmSymbolScore]:
    return {r.symbol: r for r in rankings}


def assemble_pin_universe(
    rankings: list[MmSymbolScore],
    settings: Settings,
    *,
    ticker_by_sym: dict[str, TickerVolStats] | None = None,
    sample_stats: dict[str, tuple[float, float, float]] | None = None,
) -> list[str]:
    """Liquid maincaps only — ``MM_AUTO_PIN_SYMBOLS`` after volume/spread gates (no midcaps)."""
    pins = _pin_symbols(settings)
    pin_min_vol = float(settings.mm_auto_pin_min_quote_volume)
    pin_min_edge = float(settings.mm_auto_pin_min_edge_bps)
    pin_min_spread = float(settings.mm_auto_pin_min_spread_bps)
    by_sym = _ranking_by_symbol(rankings)
    ticker_by_sym = ticker_by_sym or {}
    sample_stats = sample_stats or {}

    selected: list[str] = []
    seen: set[str] = set()

    for sym in pins:
        if sym in seen:
            continue
        row = by_sym.get(sym)
        tv = ticker_by_sym.get(sym)
        sampled = sample_stats.get(sym)
        qv = tv.quote_volume if tv else (row.quote_volume_24h if row else 0.0)
        if qv > 0 and qv < pin_min_vol:
            logger.info("flow pin: skip %s (volume %.0f < %.0f)", sym, qv, pin_min_vol)
            continue
        if row is not None and row.eligible:
            selected.append(sym)
            seen.add(sym)
            logger.info("flow pin: %s (scan eligible)", sym)
            continue
        if sampled is None:
            logger.info("flow pin: skip %s (no spread samples)", sym)
            continue
        median_sp, _spread_cv, _mid_vol = sampled
        if median_sp < pin_min_spread:
            logger.info(
                "flow pin: skip %s (spread %.2f < %.2f bps)",
                sym,
                median_sp,
                pin_min_spread,
            )
            continue
        edge = median_sp - pin_min_edge
        if edge < 0:
            logger.info("flow pin: %s tight edge (%.2f bps) — including", sym, edge)
        selected.append(sym)
        seen.add(sym)
        logger.info("flow pin: %s (spread=%.2f bps vol=%.0f)", sym, median_sp, qv)

    if not selected:
        fallback = pins[: min(5, len(pins))] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        logger.warning("flow pin universe empty; fallback=%s", fallback)
        return fallback
    logger.info("flow pin universe (%d): %s", len(selected), selected)
    return selected


def assemble_tiered_universe(
    rankings: list[MmSymbolScore],
    settings: Settings,
    *,
    ticker_by_sym: dict[str, TickerVolStats] | None = None,
    sample_stats: dict[str, tuple[float, float, float]] | None = None,
) -> list[str]:
    """Build MM universe: pinned maincaps first, then top midcaps from scan."""
    cap = int(settings.mm_auto_max_symbols)
    if cap <= 0:
        cap = 16
    pins = _pin_symbols(settings)
    mid_min_vol = float(settings.mm_auto_midcap_min_quote_volume)
    by_sym = _ranking_by_symbol(rankings)
    ticker_by_sym = ticker_by_sym or {}
    sample_stats = sample_stats or {}

    selected = assemble_pin_universe(
        rankings,
        settings,
        ticker_by_sym=ticker_by_sym,
        sample_stats=sample_stats,
    )
    if len(selected) > cap:
        selected = selected[:cap]
    seen = set(selected)

    midcap_candidates = [
        r
        for r in rankings
        if r.eligible and r.symbol not in seen and r.quote_volume_24h >= mid_min_vol
    ]
    midcap_candidates.sort(key=lambda r: r.score, reverse=True)
    for row in midcap_candidates:
        if len(selected) >= cap:
            break
        selected.append(row.symbol)
        seen.add(row.symbol)

    if not selected:
        fallback = pins[: min(3, len(pins))] or ["BTCUSDT", "ETHUSDT"]
        logger.warning("mm tiered universe empty; fallback=%s", fallback)
        return fallback
    logger.info(
        "mm tiered universe (%d): pins=%s midcaps=%s",
        len(selected),
        [s for s in selected if s in pins],
        [s for s in selected if s not in pins],
    )
    return selected


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
        wanted = set(universe)
        ticker_by_sym: dict[str, TickerVolStats] = {}
        for row in await client.ticker_24hr():
            sym = str(row.get("symbol", "")).upper()
            if sym not in wanted:
                continue
            parsed = _parse_ticker_vol_row(row)
            if parsed is not None:
                ticker_by_sym[sym] = parsed

        min_px = float(settings.mm_auto_min_mid_price)
        min_vol = float(settings.mm_auto_min_quote_volume)
        prefilter = int(settings.mm_auto_prefilter_top_volume)

        candidates: list[tuple[str, float, float]] = []
        for sym in universe:
            tv = ticker_by_sym.get(sym)
            if tv is None:
                continue
            if tv.last_price < min_px or tv.quote_volume < min_vol:
                continue
            candidates.append((sym, tv.quote_volume, tv.last_price))
        candidates.sort(key=lambda x: x[1], reverse=True)
        if prefilter > 0:
            candidates = candidates[:prefilter]

        sym_list = [c[0] for c in candidates]
        for pin in _pin_symbols(settings):
            if pin not in sym_list and pin in universe:
                sym_list.append(pin)
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

        sample_window = _sample_window_sec(settings)
        spread_cvs: list[float] = []
        mid_vols: list[float] = []
        range_vols: list[float] = []
        intraday_vols: list[float] = []
        scored_rows: list[tuple[str, float, float, float, float, float, float, float, float]] = []

        for sym, qv, px in candidates:
            sampled = sample_stats.get(sym)
            tv = ticker_by_sym.get(sym)
            range_24h = tv.range_vol_24h_bps if tv else 0.0
            if sampled is None:
                continue
            median_sp, spread_cv, sampled_mid = sampled
            effective_mid, intraday = _effective_mid_vol_bps(
                sampled_mid,
                range_24h,
                sample_window_sec=sample_window,
            )
            spread_cvs.append(spread_cv)
            mid_vols.append(effective_mid)
            range_vols.append(range_24h)
            intraday_vols.append(intraday)
            scored_rows.append(
                (sym, qv, px, median_sp, spread_cv, effective_mid, range_24h, intraday, median_sp),
            )

        thresholds = derive_stability_thresholds(
            settings,
            spread_cvs=spread_cvs,
            mid_vols=mid_vols,
            range_vols_24h=range_vols,
            intraday_vols=intraday_vols,
        )
        logger.info(
            "mm stability thresholds (source=%s, p%.0f): max_spread_cv=%.3f max_mid_vol_bps=%.2f "
            "(medians cv=%.3f mid=%.2f range24h=%.1f)",
            thresholds.source,
            thresholds.stability_percentile,
            thresholds.max_spread_cv,
            thresholds.max_mid_vol_bps,
            thresholds.spread_cv_median,
            thresholds.mid_vol_median,
            thresholds.range_vol_24h_median,
        )

        min_edge = _min_edge_bps(settings)
        rankings: list[MmSymbolScore] = []
        scored_set = {r[0] for r in scored_rows}
        for sym, qv, px in candidates:
            if sym not in scored_set:
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
        for sym, qv, px, median_sp, spread_cv, effective_mid, range_24h, intraday, _ in scored_rows:
            edge = median_sp - min_edge
            score, eligible, reason = score_mm_candidate(
                quote_volume=qv,
                median_spread_bps=median_sp,
                spread_cv=spread_cv,
                mid_vol_bps=effective_mid,
                min_volume=min_vol,
                min_spread_bps=float(settings.mm_auto_min_spread_bps),
                max_spread_bps=float(settings.mm_auto_max_spread_bps),
                max_spread_cv=thresholds.max_spread_cv,
                max_mid_vol_bps=thresholds.max_mid_vol_bps,
                min_edge_bps=min_edge,
            )
            rankings.append(
                MmSymbolScore(
                    symbol=sym,
                    quote_volume_24h=qv,
                    last_price=px,
                    median_spread_bps=median_sp,
                    spread_cv=spread_cv,
                    mid_vol_bps=effective_mid,
                    edge_bps=edge,
                    score=score,
                    eligible=eligible,
                    reject_reason=reason,
                    range_vol_24h_bps=range_24h,
                    intraday_vol_bps=intraday,
                ),
            )

        recommended = assemble_tiered_universe(
            rankings,
            settings,
            ticker_by_sym=ticker_by_sym,
            sample_stats=sample_stats,
        )

        return MmUniverseReport(
            generated_at=datetime.now(UTC).isoformat(),
            recommended=recommended,
            rankings=rankings,
            candidates_scanned=len(candidates),
            sample_rounds=int(settings.mm_auto_sample_rounds) if sample else 0,
            thresholds=thresholds,
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
    body: dict[str, Any] = {
        "generated_at": report.generated_at,
        "recommended": report.recommended,
        "candidates_scanned": report.candidates_scanned,
        "sample_rounds": report.sample_rounds,
        "rankings": [asdict(r) for r in report.rankings],
    }
    if report.thresholds is not None:
        body["thresholds"] = asdict(report.thresholds)
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
        th_raw = data.get("thresholds")
        thresholds = StabilityThresholds(**th_raw) if isinstance(th_raw, dict) else None
        return MmUniverseReport(
            generated_at=str(data.get("generated_at", "")),
            recommended=[str(s).upper() for s in data.get("recommended", [])],
            rankings=rankings,
            candidates_scanned=int(data.get("candidates_scanned", 0)),
            sample_rounds=int(data.get("sample_rounds", 0)),
            thresholds=thresholds,
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
    report = await _load_or_scan_mm_report(settings, rest=rest, force_rescan=force_rescan)
    return list(report.recommended)


async def resolve_flow_universe(
    settings: Settings | None = None,
    *,
    rest: BinanceRestClient | None = None,
    force_rescan: bool = False,
) -> list[str]:
    """Return flow-momentum symbols: full MM scan universe (pins + midcaps)."""
    report = await _load_or_scan_mm_report(settings, rest=rest, force_rescan=force_rescan)
    return list(report.recommended)


async def _load_or_scan_mm_report(
    settings: Settings | None = None,
    *,
    rest: BinanceRestClient | None = None,
    force_rescan: bool = False,
) -> MmUniverseReport:
    settings = settings or get_settings()
    ttl = float(settings.mm_auto_scan_ttl_sec)
    if not force_rescan:
        cached = load_mm_universe_report()
        if cached is not None and report_is_fresh(cached, ttl) and cached.recommended:
            logger.info(
                "mm universe: using cached report (%s, age ok)",
                cached.recommended,
            )
            return cached

    report = await scan_mm_universe(settings, rest=rest)
    write_mm_universe_report(report)
    return report


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
