from __future__ import annotations

from pathlib import Path

import pandas as pd

from analytics.kline_store import load_klines, merge_into_library


def test_merge_dedupes_by_open_time(tmp_path: Path, monkeypatch) -> None:
    import analytics.kline_store as store

    monkeypatch.setattr(store, "library_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "manifest_path", lambda: tmp_path / "manifest.json")

    t0 = pd.Timestamp("2026-01-01T00:00:00Z")
    t1 = pd.Timestamp("2026-01-01T00:01:00Z")
    df1 = pd.DataFrame(
        [
            {
                "open_time": t0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "close_time": t0 + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
                "quote_volume": 1000.0,
                "trades": 5,
                "taker_buy_base": 5.0,
                "taker_buy_quote": 500.0,
                "ignore": 0,
            }
        ]
    )
    merge_into_library(df1, "BTCUSDT", "1m", source="download")
    df2 = pd.DataFrame(
        [
            {
                "open_time": t0,
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 101.0,
                "volume": 12.0,
                "close_time": t0 + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
                "quote_volume": 1200.0,
                "trades": 6,
                "taker_buy_base": 6.0,
                "taker_buy_quote": 600.0,
                "ignore": 0,
            },
            {
                "open_time": t1,
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "volume": 8.0,
                "close_time": t1 + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1),
                "quote_volume": 800.0,
                "trades": 4,
                "taker_buy_base": 3.0,
                "taker_buy_quote": 300.0,
                "ignore": 0,
            },
        ]
    )
    merge_into_library(df2, "BTCUSDT", "1m", source="live", run_id="run-1")
    loaded = load_klines("BTCUSDT", "1m")
    assert len(loaded) == 2
    assert float(loaded.iloc[0]["close"]) == 101.0
