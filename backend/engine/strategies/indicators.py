"""Pure technical-indicator helpers for live strategies.

All functions are stateless or accept explicit prior state so strategies
can update incrementally on each closed bar without TA-Lib or REST klines.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


def ema_step(prev: float | None, price: float, period: int) -> float:
    """One-step exponential moving average (alpha = 2/(period+1))."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if prev is None:
        return price
    alpha = 2.0 / (period + 1.0)
    return price * alpha + prev * (1.0 - alpha)


def ema_seed_from_closes(closes: deque[float], prev: float | None, period: int) -> float | None:
    """EMA on bar close; seed with SMA when ``len(closes) == period``."""
    if period <= 0 or len(closes) < period:
        return None
    close = closes[-1]
    if prev is None and len(closes) == period:
        window = islice_last(closes, period)
        return sum(window) / period
    if prev is None:
        return close
    return ema_step(prev, close, period)


def wilder_alpha(period: int) -> float:
    if period <= 0:
        raise ValueError("Wilder period must be positive")
    return 1.0 / period


def wilder_step(prev: float | None, value: float, period: int) -> float:
    """Wilder smoothing (alpha = 1/period)."""
    if prev is None:
        return value
    alpha = wilder_alpha(period)
    return prev + alpha * (value - prev)


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


@dataclass(slots=True)
class RsiWilderState:
    avg_gain: float | None = None
    avg_loss: float | None = None
    prev_close: float | None = None
    seeded: bool = False


def rsi_wilder_step(state: RsiWilderState, close: float, period: int) -> float | None:
    """Wilder RSI updated once per bar close."""
    if period <= 0:
        raise ValueError("RSI period must be positive")
    if state.prev_close is None:
        state.prev_close = close
        return None
    change = close - state.prev_close
    state.prev_close = close
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    if not state.seeded:
        return None
    state.avg_gain = wilder_step(state.avg_gain, gain, period)
    state.avg_loss = wilder_step(state.avg_loss, loss, period)
    if state.avg_gain is None or state.avg_loss is None:
        return None
    if state.avg_loss <= 1e-12:
        return 100.0
    rs = state.avg_gain / state.avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_wilder_seed_from_closes(
    state: RsiWilderState, closes: deque[float], period: int
) -> float | None:
    """Initialize Wilder RSI averages from the first ``period`` bar-to-bar changes."""
    if len(closes) < period + 1:
        return None
    window = islice_last(closes, period + 1)
    gains = 0.0
    losses = 0.0
    for i in range(1, len(window)):
        change = window[i] - window[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    state.avg_gain = gains / period
    state.avg_loss = losses / period
    state.prev_close = closes[-1]
    state.seeded = True
    if state.avg_loss <= 1e-12:
        return 100.0
    rs = state.avg_gain / state.avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass(slots=True)
class AdxWilderState:
    prev_high: float | None = None
    prev_low: float | None = None
    prev_close: float | None = None
    atr: float | None = None
    plus_dm_smooth: float | None = None
    minus_dm_smooth: float | None = None
    dx_smooth: float | None = None
    adx: float | None = None
    bar_count: int = 0


def adx_wilder_step(
    state: AdxWilderState,
    *,
    high: float,
    low: float,
    close: float,
    period: int,
) -> float | None:
    """Update Wilder ADX; returns ADX once ``period`` directional bars are seeded."""
    if period <= 0:
        raise ValueError("ADX period must be positive")
    if state.prev_close is None:
        state.prev_high = high
        state.prev_low = low
        state.prev_close = close
        state.bar_count = 1
        return None

    up_move = high - (state.prev_high or high)
    down_move = (state.prev_low or low) - low
    plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
    minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

    tr = max(
        high - low,
        abs(high - state.prev_close),
        abs(low - state.prev_close),
    )
    state.prev_high = high
    state.prev_low = low
    state.prev_close = close
    state.bar_count += 1

    state.atr = wilder_step(state.atr, tr, period)
    state.plus_dm_smooth = wilder_step(state.plus_dm_smooth, plus_dm, period)
    state.minus_dm_smooth = wilder_step(state.minus_dm_smooth, minus_dm, period)

    if state.bar_count < period + 1:
        return None
    if state.atr is None or state.atr <= 1e-12:
        return None
    if state.plus_dm_smooth is None or state.minus_dm_smooth is None:
        return None

    plus_di = 100.0 * state.plus_dm_smooth / state.atr
    minus_di = 100.0 * state.minus_dm_smooth / state.atr
    di_sum = plus_di + minus_di
    if di_sum <= 1e-12:
        dx = 0.0
    else:
        dx = 100.0 * abs(plus_di - minus_di) / di_sum

    if state.dx_smooth is None:
        state.dx_smooth = dx
        return None
    state.dx_smooth = wilder_step(state.dx_smooth, dx, period)
    state.adx = wilder_step(state.adx, state.dx_smooth, period)
    return state.adx


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
