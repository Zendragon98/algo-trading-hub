"""Run a strategy offline against stored 1m klines."""

from __future__ import annotations

import json
import logging
import time as _stdlib_time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from analytics.feature_builder import features_from_row, features_from_wide_row
from analytics.kline_store import backend_data_root, load_aligned_frames, load_klines
from common.config import Settings
from engine.strategies import blended_signals as blend_mod
from engine.strategies import pairs_trading as pairs_mod
from engine.strategies import sma_crossover as sma_mod

from .metrics import BacktestMetrics, compute_metrics
from .simulator import FillSimulator, SimFill
from .strategy_factory import build_strategy, symbols_for_strategy

logger = logging.getLogger(__name__)

_TIME_PATCH_MODULES = (sma_mod, blend_mod, pairs_mod)

_BACKTEST_RUNS_DIR = backend_data_root() / "backtest_runs"


@contextmanager
def _patched_strategy_clock(clock: list[float]) -> Iterator[None]:
    """Patch strategy modules' ``time.time`` for bar replay, then restore stdlib.

    Strategies import ``time`` as a module attribute; assigning
    ``mod.time.time`` replaces the process-wide ``time.time`` until we
    put the original back — otherwise later tests see a frozen clock.
    """
    original = _stdlib_time.time

    def _fake_time() -> float:
        return clock[0]

    for mod in _TIME_PATCH_MODULES:
        mod.time.time = _fake_time
    try:
        yield
    finally:
        for mod in _TIME_PATCH_MODULES:
            mod.time.time = original


@dataclass(slots=True)
class BacktestResult:
    run_id: str
    strategy: str
    dataset: str
    bar_count: int
    symbols: list[str]
    metrics: BacktestMetrics
    equity_curve: list[float]
    fills: list[SimFill]
    notes: list[str] = field(default_factory=list)


def _resolve_run_dir(dataset: str, persist_dir: Path) -> Path | None:
    if dataset.startswith("run:"):
        run_id = dataset.removeprefix("run:").strip()
        path = persist_dir / run_id
        return path if path.is_dir() else None
    return None


def run_backtest(
    settings: Settings,
    *,
    dataset: str = "library",
    start: datetime | None = None,
    end: datetime | None = None,
    settings_overrides: dict[str, Any] | None = None,
    persist_dir: Path | None = None,
) -> BacktestResult:
    """Execute one offline backtest and optionally persist the result JSON."""
    if settings_overrides:
        settings = settings.model_copy(update=settings_overrides)
    logger.info(
        "backtest starting strategy=%s dataset=%s",
        settings.strategy,
        dataset,
    )
    persist_base = persist_dir
    if persist_base is None:
        persist_base = Path(settings.persist_dir)
        if not persist_base.is_absolute():
            persist_base = backend_data_root().parent / persist_base

    run_dir = _resolve_run_dir(dataset, persist_base)
    simulator = FillSimulator(
        initial_equity=float(settings.backtest_initial_equity),
        slippage_bps=float(settings.backtest_slippage_bps),
    )
    strategy = build_strategy(settings, simulator)
    symbols = symbols_for_strategy(settings, strategy)
    if not symbols:
        raise ValueError("strategy has no symbols configured")

    notes: list[str] = []
    if run_dir is not None:
        wide = load_aligned_frames(symbols, "1m", run_dir=run_dir, start=start, end=end)
        dataset_label = dataset
    else:
        wide = load_aligned_frames(symbols, "1m", run_dir=None, start=start, end=end)
        dataset_label = "library"

    if wide.empty:
        for sym in symbols:
            df = load_klines(sym, "1m", run_dir=run_dir, start=start, end=end)
            if not df.empty:
                notes.append(f"loaded {sym} single-leg ({len(df)} bars)")
                return _run_single_symbol(df, sym, settings, strategy, simulator, dataset_label, notes)

        raise ValueError("no kline data found for requested symbols and range")

    bar_count = len(wide)
    if bar_count < 10:
        notes.append(f"only {bar_count} bars — results may be unreliable")

    weights = getattr(strategy, "_backtest_weight_cache", None)
    clock = [0.0]

    with _patched_strategy_clock(clock):
        for _, row in wide.iterrows():
            feats = features_from_wide_row(row, symbols)
            if len(feats) < len(symbols):
                continue
            ts_vals = [f.ts for f in feats.values()]
            clock[0] = max(ts_vals) if ts_vals else clock[0]
            marks = {s: f.mid for s, f in feats.items() if f.mid is not None}
            if weights is not None:
                for sym in symbols:
                    vol = row.get(f"{sym}_volume") if hasattr(row, "get") else getattr(row, f"{sym}_volume", None)
                    if vol is not None and not (isinstance(vol, float) and pd.isna(vol)):
                        weights[sym] = float(vol)
            try:
                signals = list(strategy.on_tick(feats))
            except Exception:  # noqa: BLE001
                logger.exception("strategy on_tick failed during backtest")
                continue
            if signals:
                simulator.apply_signals(signals, marks, strategy)
            simulator.state.mark_equity(marks)

    metrics = compute_metrics(simulator.state)
    result = BacktestResult(
        run_id=str(uuid.uuid4()),
        strategy=settings.strategy,
        dataset=dataset_label,
        bar_count=bar_count,
        symbols=symbols,
        metrics=metrics,
        equity_curve=list(simulator.state.equity_curve),
        fills=list(simulator.state.fills),
        notes=notes,
    )
    _save_result(result)
    logger.info(
        "backtest complete run_id=%s bars=%d return=%.2f%% trades=%d",
        result.run_id,
        result.bar_count,
        result.metrics.total_return_pct,
        result.metrics.trade_count,
    )
    return result


def _run_single_symbol(
    df: pd.DataFrame,
    symbol: str,
    settings: Settings,
    strategy,
    simulator: FillSimulator,
    dataset_label: str,
    notes: list[str],
) -> BacktestResult:
    clock = [0.0]

    with _patched_strategy_clock(clock):
        for _, row in df.iterrows():
            feat = features_from_row(row, symbol)
            clock[0] = feat.ts
            marks = {symbol: feat.mid} if feat.mid else {}
            if not marks:
                continue
            try:
                signals = list(strategy.on_tick({symbol: feat}))
            except Exception:  # noqa: BLE001
                logger.exception("strategy on_tick failed")
                continue
            if signals:
                simulator.apply_signals(signals, marks, strategy)
            simulator.state.mark_equity(marks)
    metrics = compute_metrics(simulator.state)
    result = BacktestResult(
        run_id=str(uuid.uuid4()),
        strategy=settings.strategy,
        dataset=dataset_label,
        bar_count=len(df),
        symbols=[symbol],
        metrics=metrics,
        equity_curve=list(simulator.state.equity_curve),
        fills=list(simulator.state.fills),
        notes=notes,
    )
    _save_result(result)
    logger.info(
        "backtest complete run_id=%s symbol=%s bars=%d return=%.2f%% trades=%d",
        result.run_id,
        symbol,
        result.bar_count,
        result.metrics.total_return_pct,
        result.metrics.trade_count,
    )
    return result


def _save_result(result: BacktestResult) -> Path:
    _BACKTEST_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = _BACKTEST_RUNS_DIR / f"{result.run_id}.json"

    def _fill_dict(f: SimFill) -> dict:
        return asdict(f)

    payload = {
        "run_id": result.run_id,
        "strategy": result.strategy,
        "dataset": result.dataset,
        "bar_count": result.bar_count,
        "symbols": result.symbols,
        "metrics": asdict(result.metrics),
        "equity_curve": result.equity_curve,
        "fills": [_fill_dict(f) for f in result.fills],
        "notes": result.notes,
        "saved_at": datetime.now(tz=UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def list_saved_results() -> list[dict]:
    if not _BACKTEST_RUNS_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(_BACKTEST_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "run_id": data.get("run_id", path.stem),
                    "strategy": data.get("strategy", ""),
                    "dataset": data.get("dataset", ""),
                    "bar_count": data.get("bar_count", 0),
                    "total_return_pct": data.get("metrics", {}).get("total_return_pct", 0.0),
                    "saved_at": data.get("saved_at"),
                }
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("skipping corrupt backtest result %s: %s", path.name, exc)
            continue
    return out


def load_saved_result(run_id: str) -> dict | None:
    path = _BACKTEST_RUNS_DIR / f"{run_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
