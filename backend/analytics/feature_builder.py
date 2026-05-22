"""Build strategy ``Features`` snapshots from 1m kline rows."""

from __future__ import annotations

from typing import Any

import pandas as pd

from engine.market_data.feature_store import Features


def _f(row: Any, key: str, default: float = 0.0) -> float:
    val = row.get(key, default) if hasattr(row, "get") else getattr(row, key, default)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return float(val)


def features_from_row(row: Any, symbol: str) -> Features:
    """Convert one OHLCV row into a ``Features`` snapshot for backtest replay."""
    close = _f(row, "close")
    high = _f(row, "high", close)
    low = _f(row, "low", close)
    volume = max(_f(row, "volume"), 0.0)
    taker_buy = _f(row, "taker_buy_base", volume * 0.5)
    bid_ratio = taker_buy / volume if volume > 0 else 0.5
    ask_ratio = 1.0 - bid_ratio
    mid = close if close > 0 else (high + low) / 2.0
    spread_bps = ((high - low) / mid * 10_000.0) if mid > 0 else 0.0
    ts_val = row.get("open_time") if hasattr(row, "get") else row["open_time"]
    if hasattr(ts_val, "timestamp"):
        ts = float(ts_val.timestamp())
    else:
        ts = float(pd.Timestamp(ts_val).timestamp())
    return Features(
        symbol=symbol,
        ts=ts,
        mid=mid,
        spread_bps=spread_bps,
        micro_price=mid,
        imbalance_topn=0.0,
        bid_hit_ratio=bid_ratio,
        ask_hit_ratio=ask_ratio,
        tape_bid_hit_count=int(taker_buy),
        tape_ask_hit_count=int(max(volume - taker_buy, 0)),
        last_price=close,
        best_bid=low,
        best_ask=high,
    )


def features_from_wide_row(
    row: Any,
    symbols: list[str],
    *,
    ts: float | None = None,
) -> dict[str, Features]:
    """Build per-symbol features from a wide aligned dataframe row."""
    out: dict[str, Features] = {}
    row_ts = ts
    if row_ts is None:
        ts_val = row.get("open_time") if hasattr(row, "get") else getattr(row, "open_time", None)
        if ts_val is not None and hasattr(ts_val, "timestamp"):
            row_ts = float(ts_val.timestamp())
    for sym in symbols:
        close_key = f"{sym}_close"
        if hasattr(row, "get"):
            close = row.get(close_key)
        else:
            close = getattr(row, close_key, None)
        if close is None or (isinstance(close, float) and pd.isna(close)):
            continue
        pseudo = {
            "open_time": row.get("open_time") if hasattr(row, "get") else row["open_time"],
            "close": close,
            "high": row.get(f"{sym}_high", close) if hasattr(row, "get") else getattr(row, f"{sym}_high", close),
            "low": row.get(f"{sym}_low", close) if hasattr(row, "get") else getattr(row, f"{sym}_low", close),
            "volume": row.get(f"{sym}_volume", 1.0) if hasattr(row, "get") else getattr(row, f"{sym}_volume", 1.0),
            "taker_buy_base": row.get(f"{sym}_taker_buy", 0.5) if hasattr(row, "get") else getattr(row, f"{sym}_taker_buy", 0.5),
        }
        feat = features_from_row(pseudo, sym)
        if row_ts is not None:
            feat.ts = row_ts
        out[sym] = feat
    return out
