"""Aggregate live mids into 1m OHLCV bars for offline backtesting."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from analytics.kline_store import (
    KLINE_COLS,
    append_run_bars,
    merge_into_library,
)
from common.config import Settings
from engine.market_data.feature_store import Features

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _BuildingBar:
    bucket_start: int
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    samples: int = 0
    tape_bid_hits: int = 0
    tape_ask_hits: int = 0

    def update(self, mid: float, feat: Features) -> None:
        if self.samples == 0:
            self.open = self.high = self.low = self.close = mid
        else:
            self.high = max(self.high, mid)
            self.low = min(self.low, mid)
            self.close = mid
        self.samples += 1
        self.tape_bid_hits += int(feat.tape_bid_hit_count)
        self.tape_ask_hits += int(feat.tape_ask_hit_count)

    def to_row(self, symbol: str, interval_sec: int) -> dict:
        close_ms = (self.bucket_start + interval_sec) * 1000 - 1
        open_ms = self.bucket_start * 1000
        volume = float(max(self.samples, 1))
        total_tape = self.tape_bid_hits + self.tape_ask_hits
        if total_tape > 0:
            taker_buy = volume * (self.tape_ask_hits / total_tape)
        else:
            taker_buy = volume * 0.5
        return {
            "open_time": pd.to_datetime(open_ms, unit="ms", utc=True),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": volume,
            "close_time": pd.to_datetime(close_ms, unit="ms", utc=True),
            "quote_volume": volume * self.close,
            "trades": self.samples,
            "taker_buy_base": taker_buy,
            "taker_buy_quote": taker_buy * self.close,
            "ignore": 0,
        }


class MarketBarCapturer:
    """Build 1m bars from FeatureStore snapshots and flush to disk."""

    def __init__(
        self,
        settings: Settings,
        run_dir: Path,
        symbols: list[str],
        *,
        snapshot_fn,
    ) -> None:
        self._settings = settings
        self._run_dir = run_dir
        self._symbols = [s.upper() for s in symbols]
        self._snapshot_fn = snapshot_fn
        self._interval_sec = max(60, int(settings.capture_bar_interval_sec))
        self._flush_interval = max(60.0, float(settings.capture_flush_interval_sec))
        self._building: dict[str, _BuildingBar] = {}
        self._pending: dict[str, list[dict]] = {s: [] for s in self._symbols}
        self._last_flush_at = time.time()
        self._flush_requested = False

    def _bucket_start(self, ts: float) -> int:
        t = int(ts)
        return t - (t % self._interval_sec)

    def on_clock(self, now: float | None = None) -> None:
        """Sample features and roll closed bars. Call ~1 Hz from engine clock."""
        ts = now if now is not None else time.time()
        bucket = self._bucket_start(ts)
        for symbol in self._symbols:
            feat = self._snapshot_fn(symbol)
            mid = feat.mid
            if mid is None or mid <= 0:
                continue
            current = self._building.get(symbol)
            if current is None:
                self._building[symbol] = _BuildingBar(bucket_start=bucket)
                current = self._building[symbol]
                current.update(mid, feat)
                continue
            if bucket > current.bucket_start:
                self._pending[symbol].append(current.to_row(symbol, self._interval_sec))
                self._building[symbol] = _BuildingBar(bucket_start=bucket)
                self._building[symbol].update(mid, feat)
            else:
                current.update(mid, feat)
        if ts - self._last_flush_at >= self._flush_interval:
            self._flush_requested = True

    def flush_requested(self) -> bool:
        if not self._flush_requested:
            return False
        self._flush_requested = False
        return True

    def flush(self) -> None:
        """Write pending closed bars to per-run files and merge into library."""
        try:
            self._flush_impl()
        except Exception:  # noqa: BLE001
            logger.exception("market capture flush failed")

    def _flush_impl(self) -> None:
        run_id = self._run_dir.name
        for symbol, rows in self._pending.items():
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=KLINE_COLS)
            append_run_bars(df, self._run_dir, symbol, "1m")
            merge_into_library(df, symbol, "1m", source="live", run_id=run_id)
            rows.clear()
        if self._building:
            partial: dict[str, list[dict]] = {}
            for symbol, bar in self._building.items():
                if bar.samples > 0:
                    partial.setdefault(symbol, []).append(bar.to_row(symbol, self._interval_sec))
            for symbol, rows in partial.items():
                df = pd.DataFrame(rows, columns=KLINE_COLS)
                append_run_bars(df, self._run_dir, symbol, "1m")
            logger.debug("market capture flush (%d symbols)", len(self._building))

    def refresh_symbols(self, symbols: list[str]) -> None:
        self._symbols = [s.upper() for s in symbols]
        for sym in self._symbols:
            self._pending.setdefault(sym, [])


def should_capture(settings: Settings) -> bool:
    return bool(settings.persist_enabled and settings.capture_market_bars)


def create_capturer(
    settings: Settings,
    run_dir: Path,
    symbols: list[str],
    snapshot_fn,
) -> MarketBarCapturer | None:
    if not should_capture(settings):
        return None
    logger.info(
        "market bar capturer created (run=%s, symbols=%d, interval=%ds)",
        run_dir.name,
        len(symbols),
        max(60, int(settings.capture_bar_interval_sec)),
    )
    return MarketBarCapturer(settings, run_dir, symbols, snapshot_fn=snapshot_fn)
