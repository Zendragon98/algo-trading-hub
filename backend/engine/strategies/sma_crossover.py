"""Multi-symbol SMA crossover strategy.

The classic fast/slow simple-moving-average crossover, applied
*independently* to every configured symbol. Whenever the fast line
crosses above the slow line on coin X we go long X; when it crosses
back below we flip short. Each symbol carries its own deque of mids
and its own cooldown so a flap on BTC never interferes with an ETH
crossover.

Sizing is equity-budgeted: each entry risks ``sma_risk_per_trade_pct``
of equity, sized via the per-leg stop loss (``default_stop_loss_pct``)
so a stop-out costs the same dollar amount regardless of price level.
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
from dataclasses import dataclass, field
from itertools import islice

from common.config import Settings
from common.enums import Side
from common.logging import signal_log
from common.types import Signal

from ..market_data.feature_store import Features
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)


# Same shape as PairsTradingStrategy.EquityProvider — the engine wires
# an ``engine.portfolio.snapshot().equity`` reader in via
# ``attach_equity_provider``.
EquityProvider = Callable[[], float]


@dataclass(slots=True)
class _SymbolState:
    """Per-symbol rolling buffer + crossover memory."""

    mids: deque[float]
    prev_fast_above: bool | None = None
    # +1 long, -1 short, 0 flat (in *strategy intent* — the actual
    # position can lag pending OMS fills).
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
        # Per-symbol state lazily upgraded through ``_state_for`` so a
        # mid-run universe expansion (settings hot-reload) doesn't lose
        # any existing crossover memory.
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        """Pick the symbols this strategy scans.

        Preference order:
            1. ``settings.sma_symbols`` (CSV / AUTO-resolved at boot).
            2. ``settings.sma_symbol`` (legacy single-symbol setting).
            3. ``["BTCUSDT"]`` as the deterministic last-resort default.
        """
        configured = [s.strip().upper() for s in (settings.sma_symbols or []) if s.strip()]
        if configured:
            return sorted(set(configured))
        legacy = (settings.sma_symbol or "").strip().upper()
        if legacy:
            return [legacy]
        return ["BTCUSDT"]

    # --- Public hooks ---

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        """Wire the live equity reader so entries can be equity-budgeted."""
        self._equity_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.sma_cooldown_sec)
        signals: list[Signal] = []

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None or feat.mid <= 0:
                continue
            mid = float(feat.mid)
            state = self._state_for(symbol)

            if now - state.last_action_ts < cooldown:
                state.append(mid)
                continue

            state.append(mid)
            if len(state.mids) < self._slow:
                continue

            slow_avg = sum(state.mids) / self._slow
            # ``islice`` over the deque avoids materialising the full list
            # every tick; matters when SMA_SYMBOLS=AUTO covers ~545 perps.
            fast_window = islice(state.mids, len(state.mids) - self._fast, len(state.mids))
            fast_avg = sum(fast_window) / self._fast

            diff = fast_avg - slow_avg
            # Floating point noise can flip fast/slow around equality and
            # spawn spurious crossovers; treat near-equality as no change.
            if abs(diff) <= 1e-9 and state.prev_fast_above is not None:
                fast_above = state.prev_fast_above
            else:
                fast_above = diff > 0

            if state.prev_fast_above is None:
                state.prev_fast_above = fast_above
                continue

            crossed_up = (not state.prev_fast_above) and fast_above
            crossed_down = state.prev_fast_above and (not fast_above)
            state.prev_fast_above = fast_above

            qty = self._size_for(mid)
            if qty <= 0:
                continue

            if crossed_up and state.open_side != +1:
                state.open_side = +1
                state.last_action_ts = now
                reason = f"sma_cross_up fast={fast_avg:.6f} slow={slow_avg:.6f}"
                signal_log(logger, f"SMA cross up -> BUY {symbol} qty={qty:.10f}")
                signals.append(Signal(symbol=symbol, side=Side.BUY, qty=qty, reason=reason, score=1.0))
                continue

            if crossed_down and state.open_side != -1:
                state.open_side = -1
                state.last_action_ts = now
                reason = f"sma_cross_down fast={fast_avg:.6f} slow={slow_avg:.6f}"
                signal_log(logger, f"SMA cross down -> SELL {symbol} qty={qty:.10f}")
                signals.append(Signal(symbol=symbol, side=Side.SELL, qty=qty, reason=reason, score=1.0))

        return signals

    # --- Internal ---

    def _state_for(self, symbol: str) -> _SymbolState:
        state = self._state.get(symbol)
        if state is None:
            state = _SymbolState(mids=deque(maxlen=self._slow))
            self._state[symbol] = state
        return state

    def _size_for(self, mid: float) -> float:
        """Return the qty the strategy wants to buy/sell at ``mid``.

        Equity-budgeted: ``equity * risk_per_trade_pct / stop_pct / mid``
        when the equity reader is wired, else a flat ``sma_qty`` so the
        strategy still places (small) orders in smoke tests / before the
        first ``fetch_balance`` lands.
        """
        provider = self._equity_provider
        if provider is None:
            return float(self._settings.sma_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001 — defensive; never let sizing crash the loop
            return float(self._settings.sma_qty)
        if equity <= 0:
            return float(self._settings.sma_qty)
        risk_pct = float(self._settings.sma_risk_per_trade_pct)
        stop_pct = float(self._settings.default_stop_loss_pct)
        if risk_pct <= 0 or stop_pct <= 0 or mid <= 0:
            return float(self._settings.sma_qty)
        # Stop-loss-budgeted notional, then divide by mid to get qty.
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)
