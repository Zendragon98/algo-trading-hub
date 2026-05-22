"""Periodic and adverse-triggered MM universe refresh for ``MM_SYMBOLS=AUTO``."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from common.config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SymbolMicroSnapshot:
    markout_adverse_ewma_bps: float = 0.0
    is_toxic: bool = False
    jump_active: bool = False
    spread_bps: float | None = None
    vol_ewma_bps: float = 0.0
    mid_return_1s_bps: float = 0.0


@dataclass(slots=True)
class AdverseUniverseSignal:
    reason: str
    symbols: list[str]
    detail: str


def mm_auto_active(settings: Settings) -> bool:
    return bool(settings.mm_universe_auto or settings.mm2_universe_auto)


def spread_baselines_from_report(path_symbols: dict[str, float]) -> dict[str, float]:
    return {k.upper(): float(v) for k, v in path_symbols.items() if v > 0}


def load_spread_baselines() -> dict[str, float]:
    from .mm_universe_scanner import load_mm_universe_report

    report = load_mm_universe_report()
    if report is None:
        return {}
    return {
        r.symbol: r.median_spread_bps
        for r in report.rankings
        if r.median_spread_bps > 0
    }


def _markout_threshold(settings: Settings) -> float:
    explicit = float(settings.mm_universe_adverse_markout_bps)
    if explicit > 0:
        return explicit
    return float(settings.mm_max_adverse_markout_bps)


def evaluate_adverse_universe(
    symbols: list[str],
    features: dict[str, SymbolMicroSnapshot],
    *,
    settings: Settings,
    spread_baselines: dict[str, float],
) -> AdverseUniverseSignal | None:
    """Return a refresh signal when enough MM symbols show adverse microstructure."""
    if not symbols:
        return None

    markout_thresh = _markout_threshold(settings)
    min_syms = max(1, int(settings.mm_universe_adverse_min_symbols))
    spread_mult = float(settings.mm_universe_adverse_spread_widen_mult)
    regime_vol = float(settings.mm_universe_adverse_regime_vol_bps)
    regime_refs = [s.strip().upper() for s in settings.mm_universe_regime_symbols if s.strip()]

    markout_hits: list[str] = []
    toxic_hits: list[str] = []
    jump_hits: list[str] = []
    spread_hits: list[str] = []

    for sym in symbols:
        feat = features.get(sym)
        if feat is None:
            continue
        if feat.markout_adverse_ewma_bps >= markout_thresh:
            markout_hits.append(sym)
        if feat.is_toxic:
            toxic_hits.append(sym)
        if feat.jump_active:
            jump_hits.append(sym)
        base = spread_baselines.get(sym)
        sp = feat.spread_bps
        if base and base > 0 and sp is not None and sp > base * spread_mult:
            spread_hits.append(sym)

    if len(markout_hits) >= min_syms:
        return AdverseUniverseSignal(
            reason="adverse_markout",
            symbols=markout_hits,
            detail=f"{len(markout_hits)} symbol(s) markout ewma >= {markout_thresh:.1f} bps",
        )
    if len(toxic_hits) >= min_syms:
        return AdverseUniverseSignal(
            reason="toxic_flow",
            symbols=toxic_hits,
            detail=f"{len(toxic_hits)} symbol(s) toxic",
        )
    if len(jump_hits) >= min_syms:
        return AdverseUniverseSignal(
            reason="jump_vol",
            symbols=jump_hits,
            detail=f"{len(jump_hits)} symbol(s) jump_active",
        )
    if len(spread_hits) >= min_syms:
        return AdverseUniverseSignal(
            reason="spread_blowout",
            symbols=spread_hits,
            detail=f"{len(spread_hits)} symbol(s) spread > {spread_mult:.2f}x scan baseline",
        )

    for ref in regime_refs:
        feat = features.get(ref)
        if feat is None:
            continue
        if feat.vol_ewma_bps >= regime_vol:
            return AdverseUniverseSignal(
                reason="regime_vol",
                symbols=[ref],
                detail=f"{ref} vol_ewma={feat.vol_ewma_bps:.1f} bps >= {regime_vol:.1f}",
            )
        if abs(feat.mid_return_1s_bps) >= regime_vol * 2.0:
            return AdverseUniverseSignal(
                reason="regime_shock",
                symbols=[ref],
                detail=f"{ref} mid_return_1s={feat.mid_return_1s_bps:.1f} bps",
            )

    return None


def should_run_periodic_refresh(
    *,
    last_refresh_ts: float,
    refresh_sec: float,
    now: float | None = None,
) -> bool:
    now = now if now is not None else time.time()
    interval = float(refresh_sec)
    if interval <= 0:
        return False
    return (now - last_refresh_ts) >= interval


def should_run_adverse_refresh(
    *,
    last_adverse_refresh_ts: float,
    cooldown_sec: float,
    now: float | None = None,
) -> bool:
    now = now if now is not None else time.time()
    cooldown = float(cooldown_sec)
    if cooldown <= 0:
        return True
    return (now - last_adverse_refresh_ts) >= cooldown
