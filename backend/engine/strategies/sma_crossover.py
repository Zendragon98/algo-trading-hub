"""Multi-symbol SMA crossover strategy.

The classic fast/slow simple-moving-average crossover, applied
*independently* to every configured symbol. Whenever the fast line
crosses above the slow line on coin X we go long X; when it crosses
back below we flip short. Each symbol carries its own deque of mids
and its own cooldown so a flap on BTC never interferes with an ETH
crossover.

Samples can be either one mid per engine heartbeat (default) or one
closed bar per ``sma_bar_interval_sec`` (set e.g. to ``300`` for 5m bars
so windows mean “bars”, not seconds — better when spread must be
recovered over larger moves).

Sizing is equity-budgeted: the portfolio budgets ``sma_risk_per_trade_pct``
of equity across the full symbol universe (each leg gets an equal slice),
sized via the per-leg stop loss (``default_stop_loss_pct``) so a simultaneous
stop-out on every leg would lose about that budget, not N× the budget.
A static ``sma_qty`` fallback is used while equity is unavailable
(boot, REST hiccup) so the strategy can still smoke-test the OMS.

The strategy is execution-correctness-friendly: it does not manage its
own SL/TP (``manages_own_risk()`` returns False) so the engine's
per-leg StopLossMonitor stays armed.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice

from common.config import Settings
from common.logging import signal_log
from common.types import Signal

from ..market_data.feature_store import Features
from .position_sync import plan_directional_signal, side_from_qty
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)


EquityProvider = Callable[[], float]


@dataclass(slots=True)
class _SymbolState:
    """Per-symbol rolling buffer + crossover memory."""

    mids: deque[float]
    last_mid_in_bar: float = 0.0
    bar_bucket: int | None = None
    prev_fast_above: bool | None = None
    open_side: int = 0
    last_action_ts: float = 0.0

    def append(self, mid: float) -> None:
        self.mids.append(mid)


class SmaCrossoverStrategy(StrategyBase):
    name = "sma_crossover"
    display_label = "SMA Crossover"
    description = "Fast/slow SMA crossover scanner across the configured universe"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fast = int(settings.sma_fast_window)
        self._slow = int(settings.sma_slow_window)
        if self._fast <= 0 or self._slow <= 0:
            raise ValueError("SMA windows must be positive")
        if self._fast >= self._slow:
            raise ValueError("SMA_FAST_WINDOW must be < SMA_SLOW_WINDOW")

        self._symbols: list[str] = self._resolve_universe(settings)
        self._bar_interval_sec = float(settings.sma_bar_interval_sec or 0.0)
        if self._bar_interval_sec < 0:
            raise ValueError("SMA_BAR_INTERVAL_SEC must be >= 0")
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._last_scan_log_ts: float = 0.0

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [s.strip().upper() for s in (settings.sma_symbols or []) if s.strip()]
        if configured:
            return sorted(set(configured))
        legacy = (settings.sma_symbol or "").strip().upper()
        if legacy:
            return [legacy]
        return ["BTCUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._fast = int(settings.sma_fast_window)
        self._slow = int(settings.sma_slow_window)
        if self._fast <= 0 or self._slow <= 0 or self._fast >= self._slow:
            raise ValueError("SMA_FAST_WINDOW must be positive and less than SMA_SLOW_WINDOW")
        self._bar_interval_sec = float(settings.sma_bar_interval_sec or 0.0)
        if self._bar_interval_sec < 0:
            raise ValueError("SMA_BAR_INTERVAL_SEC must be >= 0")
        self._symbols = self._resolve_universe(settings)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        state = self._state.get(symbol)
        if state is not None:
            self._sync_open_side_from_position(state, symbol)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.sma_cooldown_sec)
        signals: list[Signal] = []
        quoted = warming = ready = in_cooldown = bullish = bearish = 0

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None or feat.mid <= 0:
                continue
            mid = float(feat.mid)
            if mid < float(self._settings.sma_min_mid_price):
                continue
            quoted += 1
            state = self._state_for(symbol)
            self._sync_open_side_from_position(state, symbol)

            if now - state.last_action_ts < cooldown:
                in_cooldown += 1
                self._push_sample(state, mid, now)
                continue

            if not self._push_sample(state, mid, now):
                continue
            if len(state.mids) < self._slow:
                warming += 1
                continue

            ready += 1
            slow_avg = sum(state.mids) / self._slow
            fast_window = islice(state.mids, len(state.mids) - self._fast, len(state.mids))
            fast_avg = sum(fast_window) / self._fast

            diff = fast_avg - slow_avg
            if abs(diff) <= 1e-9 and state.prev_fast_above is not None:
                fast_above = state.prev_fast_above
            else:
                fast_above = diff > 0

            if fast_above:
                bullish += 1
            else:
                bearish += 1

            if state.prev_fast_above is None:
                state.prev_fast_above = fast_above
                continue

            crossed_up = (not state.prev_fast_above) and fast_above
            crossed_down = state.prev_fast_above and (not fast_above)
            state.prev_fast_above = fast_above

            entry_qty = self._size_for(mid)
            if entry_qty <= 0:
                continue

            pos_qty = self._position_qty(symbol)
            sig: Signal | None = None
            if crossed_up and side_from_qty(pos_qty) != 1:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=+1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=f"sma_cross_up fast={fast_avg:.6f} slow={slow_avg:.6f}",
                    reason_close="sma_cross_up_close",
                    score=1.0,
                )
            elif crossed_down and side_from_qty(pos_qty) != -1:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=-1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=f"sma_cross_down fast={fast_avg:.6f} slow={slow_avg:.6f}",
                    reason_close="sma_cross_down_close",
                    score=1.0,
                )

            if sig is None:
                continue
            state.last_action_ts = now
            if not sig.reduce_only:
                state.open_side = 1 if sig.side.value.lower() == "buy" else -1
            signal_log(
                logger,
                f"SMA {'close' if sig.reduce_only else 'open'} -> {sig.side.value.upper()} "
                f"{symbol} qty={sig.qty:.10f}",
            )
            signals.append(sig)

        signals = self._cap_entries(signals)
        self._maybe_log_scan_heartbeat(
            now=now,
            quoted=quoted,
            warming=warming,
            ready=ready,
            in_cooldown=in_cooldown,
            bullish=bullish,
            bearish=bearish,
            signal_count=len(signals),
        )
        return signals

    def _cap_entries(self, signals: list[Signal]) -> list[Signal]:
        max_n = int(getattr(self._settings, "sma_max_entries_per_tick", 0) or 0)
        if max_n <= 0:
            return signals
        exits = [s for s in signals if s.reduce_only]
        entries = [s for s in signals if not s.reduce_only]
        if len(entries) <= max_n:
            return signals
        entries.sort(key=lambda s: -float(s.score))
        return exits + entries[:max_n]

    def _maybe_log_scan_heartbeat(
        self,
        *,
        now: float,
        quoted: int,
        warming: int,
        ready: int,
        in_cooldown: int,
        bullish: int,
        bearish: int,
        signal_count: int,
    ) -> None:
        interval = float(self._settings.sma_scan_log_interval_sec)
        if interval <= 0:
            return
        if self._last_scan_log_ts > 0 and now - self._last_scan_log_ts < interval:
            return
        self._last_scan_log_ts = now
        sample_mode = (
            f"bar={int(self._bar_interval_sec)}s"
            if self._bar_interval_sec > 0
            else "tick=1Hz"
        )
        logger.info(
            "SMA scan heartbeat: universe=%d quoted=%d ready=%d warming=%d "
            "cooldown=%d bullish=%d bearish=%d %s fast=%d slow=%d signals=%d",
            len(self._symbols),
            quoted,
            ready,
            warming,
            in_cooldown,
            bullish,
            bearish,
            sample_mode,
            self._fast,
            self._slow,
            signal_count,
        )

    def _push_sample(self, state: _SymbolState, mid: float, now: float) -> bool:
        if self._bar_interval_sec <= 0:
            state.append(mid)
            return True
        return self._append_bar_close_if_advanced(state, mid, now)

    def _append_bar_close_if_advanced(self, state: _SymbolState, mid: float, now: float) -> bool:
        interval = self._bar_interval_sec
        bucket = int(now // interval)
        if state.bar_bucket is None:
            state.bar_bucket = bucket
            state.last_mid_in_bar = mid
            return False
        if bucket == state.bar_bucket:
            state.last_mid_in_bar = mid
            return False
        close_px = state.last_mid_in_bar
        state.append(close_px)
        state.bar_bucket = bucket
        state.last_mid_in_bar = mid
        return True

    def _state_for(self, symbol: str) -> _SymbolState:
        state = self._state.get(symbol)
        if state is None:
            state = _SymbolState(mids=deque(maxlen=self._slow))
            self._state[symbol] = state
        return state

    def _size_for(self, mid: float) -> float:
        provider = self._equity_provider
        if provider is None:
            return float(self._settings.sma_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001
            return float(self._settings.sma_qty)
        if equity <= 0:
            return float(self._settings.sma_qty)
        universe_n = max(1, len(self._symbols))
        risk_pct = float(self._settings.sma_risk_per_trade_pct) / universe_n
        stop_pct = float(self._settings.default_stop_loss_pct)
        if risk_pct <= 0 or stop_pct <= 0 or mid <= 0:
            return float(self._settings.sma_qty)
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)
