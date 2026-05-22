"""Pure technical-indicator helpers for live strategies.

All functions are stateless or accept explicit prior state so strategies
can update incrementally on each closed bar without TA-Lib or REST klines.
"""

from __future__ import annotations

import math
from collections import deque


def ema_step(prev: float | None, price: float, period: int) -> float:
    """One-step exponential moving average."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if prev is None:
        return price
    alpha = 2.0 / (period + 1.0)
    return price * alpha + prev * (1.0 - alpha)


def sma(values: deque[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def std_dev(values: deque[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    return math.sqrt(var)


def rsi_from_closes(closes: deque[float], period: int) -> float | None:
    """RSI from the last ``period`` price changes (simple average gain/loss)."""
    if period <= 0 or len(closes) < period + 1:
        return None
    window = islice_last(closes, period + 1)
    gains = 0.0
    losses = 0.0
    for i in range(1, len(window)):
        change = window[i] - window[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_bands(
    closes: deque[float],
    *,
    period: int,
    std_mult: float,
) -> tuple[float, float, float, float] | None:
    """Return (middle, upper, lower, pct_b) for the latest close."""
    if period <= 0 or len(closes) < period:
        return None
    window = deque(islice_last(closes, period))
    mid = sum(window) / period
    sd = std_dev(window)
    if sd is None:
        return None
    upper = mid + std_mult * sd
    lower = mid - std_mult * sd
    price = closes[-1]
    width = upper - lower
    if width <= 1e-12:
        pct_b = 0.5
    else:
        pct_b = (price - lower) / width
    return mid, upper, lower, pct_b


def macd_step(
    *,
    ema_fast: float | None,
    ema_slow: float | None,
    signal: float | None,
    price: float,
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> tuple[float, float, float, float, float, float]:
    """Update MACD; returns (macd, signal, hist, new_signal, new_fast_ema, new_slow_ema)."""
    fast = ema_step(ema_fast, price, fast_period)
    slow = ema_step(ema_slow, price, slow_period)
    macd_line = fast - slow
    sig = ema_step(signal, macd_line, signal_period)
    hist = macd_line - sig
    return macd_line, sig, hist, sig, fast, slow


def islice_last(d: deque[float], n: int) -> list[float]:
    if n <= 0:
        return []
    if n >= len(d):
        return list(d)
    start = len(d) - n
    return [d[i] for i in range(start, len(d))]
