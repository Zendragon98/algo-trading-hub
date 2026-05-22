"""Rolling mid returns, volatility EWMA, and jump detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from common.config import Settings


@dataclass(slots=True)
class MidStats:
    return_1s_bps: float = 0.0
    return_5s_bps: float = 0.0
    vol_ewma_bps: float = 0.0
    jump_active: bool = False
    jump_pause_until: float = 0.0


class MidReturnTracker:
    def __init__(self, settings: Settings) -> None:
        self.apply_settings(settings)
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._vol_ewma: dict[str, float] = {}
        self._pause_until: dict[str, float] = {}

    def apply_settings(self, settings: Settings) -> None:
        self._jump_bps = float(settings.mm_jump_return_bps)
        self._jump_vol_mult = float(settings.mm_jump_vol_mult)
        self._pause_sec = float(settings.mm_jump_pause_sec)
        self._vol_alpha = max(1e-6, min(1.0, float(settings.mm_jump_vol_ewma_alpha)))

    def on_mid(self, symbol: str, mid: float, ts: float) -> None:
        if mid <= 0:
            return
        hist = self._history.setdefault(symbol, deque(maxlen=512))
        hist.append((ts, mid))
        if len(hist) < 2:
            return
        prev_ts, prev_mid = hist[-2]
        if prev_mid <= 0 or ts <= prev_ts:
            return
        dt = ts - prev_ts
        if dt <= 0:
            return
        ret_bps = (mid - prev_mid) / prev_mid * 10_000.0
        prev_vol = self._vol_ewma.get(symbol, abs(ret_bps))
        self._vol_ewma[symbol] = self._vol_alpha * abs(ret_bps) + (1.0 - self._vol_alpha) * prev_vol
        vol = self._vol_ewma[symbol]
        jump = abs(ret_bps) > self._jump_bps or (
            self._jump_vol_mult > 0 and abs(ret_bps) > self._jump_vol_mult * max(vol, 1e-6)
        )
        if jump:
            self._pause_until[symbol] = ts + self._pause_sec

    def stats(self, symbol: str, *, now: float) -> MidStats:
        hist = self._history.get(symbol)
        pause = self._pause_until.get(symbol, 0.0)
        jump_active = now < pause if pause > 0 else False
        if not hist or len(hist) < 2:
            return MidStats(jump_active=jump_active, jump_pause_until=pause)

        mid_now = hist[-1][1]
        r1 = _return_bps_over(hist, mid_now, now, 1.0)
        r5 = _return_bps_over(hist, mid_now, now, 5.0)
        return MidStats(
            return_1s_bps=r1,
            return_5s_bps=r5,
            vol_ewma_bps=self._vol_ewma.get(symbol, 0.0),
            jump_active=jump_active,
            jump_pause_until=pause,
        )


def _return_bps_over(
    hist: deque[tuple[float, float]], mid_now: float, now: float, window_sec: float,
) -> float:
    cutoff = now - window_sec
    ref_mid = mid_now
    for ts, mid in reversed(hist):
        if ts <= cutoff:
            ref_mid = mid
            break
    if ref_mid <= 0:
        return 0.0
    return (mid_now - ref_mid) / ref_mid * 10_000.0
