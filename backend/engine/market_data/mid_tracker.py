"""Rolling mid returns, volatility EWMA, and jump detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from common.config import Settings

from ..strategies.mm_calibrated import mm_risk_float


@dataclass(slots=True)
class MidStats:
    return_1s_bps: float = 0.0
    return_5s_bps: float = 0.0
    vol_ewma_bps: float = 0.0
    vol_5m_bps: float = 0.0
    vol_1h_bps: float = 0.0
    jump_active: bool = False
    jump_pause_until: float = 0.0


class MidReturnTracker:
    def __init__(self, settings: Settings) -> None:
        self.apply_settings(settings)
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._vol_ewma: dict[str, float] = {}
        self._vol_slow: dict[str, float] = {}
        self._pause_until: dict[str, float] = {}

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._pause_sec = float(settings.mm_jump_pause_sec)
        self._vol_alpha = max(1e-6, min(1.0, float(settings.mm_jump_vol_ewma_alpha)))
        self._vol_slow_alpha = 0.0003

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
        prev_slow = self._vol_slow.get(symbol, abs(ret_bps))
        self._vol_slow[symbol] = self._vol_slow_alpha * abs(ret_bps) + (
            1.0 - self._vol_slow_alpha
        ) * prev_slow
        vol = self._vol_ewma[symbol]
        jump_bps = mm_risk_float(
            symbol,
            self._settings,
            "mm_jump_return_bps",
            cal_attr="jump_return_bps",
        )
        jump_vol_mult = mm_risk_float(
            symbol,
            self._settings,
            "mm_jump_vol_mult",
            cal_attr="jump_vol_mult",
        )
        # Floor EWMA so vol-relative jumps do not fire on sub-bps noise at boot.
        vol_for_jump = max(vol, 5.0)
        jump = abs(ret_bps) > jump_bps or (
            jump_vol_mult > 0 and abs(ret_bps) > jump_vol_mult * vol_for_jump
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
        vol_5m = _realized_vol_bps(hist, now, 300.0)
        return MidStats(
            return_1s_bps=r1,
            return_5s_bps=r5,
            vol_ewma_bps=self._vol_ewma.get(symbol, 0.0),
            vol_5m_bps=vol_5m,
            vol_1h_bps=self._vol_slow.get(symbol, 0.0),
            jump_active=jump_active,
            jump_pause_until=pause,
        )


def _realized_vol_bps(hist: deque[tuple[float, float]], now: float, window_sec: float) -> float:
    cutoff = now - window_sec
    rets: list[float] = []
    prev_mid: float | None = None
    prev_ts: float | None = None
    for ts, mid in hist:
        if ts < cutoff:
            prev_mid = mid
            prev_ts = ts
            continue
        if prev_mid is not None and prev_mid > 0 and prev_ts is not None and ts > prev_ts:
            rets.append((mid - prev_mid) / prev_mid * 10_000.0)
        prev_mid = mid
        prev_ts = ts
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var**0.5


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
