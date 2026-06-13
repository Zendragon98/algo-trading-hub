from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from analytics.backtest.runner import run_backtest  # noqa: E402
from analytics.kline_store import merge_into_library  # noqa: E402
from common.config import Settings  # noqa: E402


def _make_uptrend_bars(n: int = 40) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    for i in range(n):
        close = 100.0 + i * 0.5
        t = base + pd.Timedelta(minutes=i)
        rows.append(
            {
                "open_time": t,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 10.0,
                "close_time": t + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
                "quote_volume": close * 10,
                "trades": 5,
                "taker_buy_base": 6.0,
                "taker_buy_quote": close * 6,
                "ignore": 0,
            }
        )
    return pd.DataFrame(rows)


def test_backtest_sma_on_synthetic_klines(tmp_path: Path, monkeypatch) -> None:
    import analytics.kline_store as store

    monkeypatch.setattr(store, "library_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "manifest_path", lambda: tmp_path / "manifest.json")

    df = _make_uptrend_bars(50)
    merge_into_library(df, "BTCUSDT", "1m", source="download")

    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        strategy="sma",
        sma_symbols=["BTCUSDT"],
        sma_symbol="BTCUSDT",
        sma_fast_window=3,
        sma_slow_window=5,
        sma_qty=1.0,
        sma_bar_interval_sec=0.0,
        sma_cooldown_sec=0,
    )
    result = run_backtest(settings, dataset="library")
    assert result.bar_count >= 40
    assert len(result.equity_curve) > 0


def test_market_capturer_builds_bar(tmp_path) -> None:
    from common.config import Settings
    from engine.market_data.feature_store import Features
    from engine.persistence.market_capture import MarketBarCapturer

    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        capture_bar_interval_sec=60,
        capture_flush_interval_sec=60,
    )
    run_dir = tmp_path / "test_capture_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    feats = [
        Features(symbol="BTCUSDT", mid=100.0, spread_bps=1.0),
        Features(symbol="BTCUSDT", mid=101.0, spread_bps=1.0),
    ]
    idx = [0]

    def snap(sym: str) -> Features:
        return feats[min(idx[0], len(feats) - 1)]

    cap = MarketBarCapturer(settings, run_dir, ["BTCUSDT"], snapshot_fn=snap)
    cap.on_clock(1_700_000_000.0)
    idx[0] = 1
    cap.on_clock(1_700_000_030.0)
    cap.on_clock(1_700_000_060.0)
    assert cap._pending["BTCUSDT"]
