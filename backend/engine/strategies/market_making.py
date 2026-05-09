"""Microstructure-tilt strategy inspired by market-making signals.

Combines **rolling micro-price skew**, **order-book imbalance**, and
**aggressive trade counts** from the venue tape. Execution stays the
engine's normal directional ``BUY``/``SELL`` path (VWAP-style), not a
custom quote ladder.

* **Skew** each tick: ``(micro_price - mid) / mid * 10_000`` bps (book
  must be ready). **5-minute skew** = mean of skew samples in the last
  ``mm_skew_window_sec`` (default 300s).
* **Imbalance**: ``imbalance_topn`` from the L2 book.
* **Tape pressure** (directional aggressor activity): uses
  ``tape_ask_hit_count`` and ``tape_bid_hit_count`` on ``Features`` —
  how many trades hit the bid (seller-initiated) vs lifted the offer
  (buyer-initiated) inside the **same rolling window** as
  ``trade_tape_window_sec`` (default 300s / 5 minutes). Normalised to
  ``(asks - bids) / total`` in ``[-1, 1]`` when
  ``total >= mm_min_tape_trades``; otherwise contributes ``0`` so thin
  tape does not dominate.

* **Composite** = ``mm_skew_scale * skew_avg + mm_imbalance_scale * imbalance
  + mm_tape_scale * tape_pressure``.

``mm_signal_mode``:

* ``fade`` (default): buy when composite is very *negative*, sell when
  very *positive*.
* ``follow``: buy on positive composite, sell on negative (short-term
  continuation).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import Settings
from common.enums import Side
from common.logging import signal_log
from common.types import Signal

from ..market_data.feature_store import Features
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]


@dataclass(slots=True)
class _SymbolState:
    skew_samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=4096)
    )
    open_side: int = 0
    last_action_ts: float = 0.0


class MarketMakingStrategy(StrategyBase):
    name = "market_making"
    display_label = "Market making (skew + book + tape)"
    description = (
        "Skew, L2 imbalance, and bid-hit vs offer-lift counts (tape window); "
        "fade or follow via MM_SIGNAL_MODE"
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm_skew_window_sec)
        if self._window <= 0:
            raise ValueError("MM_SKEW_WINDOW_SEC must be positive")
        self._symbols = self._resolve_universe(settings)
        mode = (settings.mm_signal_mode or "fade").strip().lower()
        if mode not in ("fade", "follow"):
            raise ValueError("MM_SIGNAL_MODE must be 'fade' or 'follow'")
        self._fade = mode == "fade"
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        w = float(settings.mm_skew_window_sec)
        if w <= 0:
            raise ValueError("MM_SKEW_WINDOW_SEC must be positive")
        self._window = w
        mode = (settings.mm_signal_mode or "fade").strip().lower()
        if mode not in ("fade", "follow"):
            raise ValueError("MM_SIGNAL_MODE must be 'fade' or 'follow'")
        self._fade = mode == "fade"
        self._symbols = self._resolve_universe(settings)

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [s.strip().upper() for s in (settings.mm_symbols or []) if s.strip()]
        if configured:
            return sorted(set(configured))
        if settings.symbols:
            return [str(s).strip().upper() for s in settings.symbols if str(s).strip()][:1] or ["BTCUSDT"]
        return ["BTCUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.mm_cooldown_sec)
        entry = float(self._settings.mm_entry_tilt)
        if entry <= 0:
            return []
        signals: list[Signal] = []

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None:
                continue
            state = self._state_for(symbol)

            if now - state.last_action_ts < cooldown:
                self._record_skew(state, feat, now)
                continue

            self._record_skew(state, feat, now)
            skew_avg = self._skew_mean(state, now)
            if skew_avg is None:
                continue

            imb = float(feat.imbalance_topn)
            tape_p = self._tape_pressure(feat)
            comp = (
                float(self._settings.mm_skew_scale) * skew_avg
                + float(self._settings.mm_imbalance_scale) * imb
                + float(self._settings.mm_tape_scale) * tape_p
            )

            qty = self._size_for(feat.mid or 0.0)
            if qty <= 0:
                continue

            want_buy, want_sell = self._desired_sides(comp, entry)

            if want_buy and state.open_side != +1:
                state.open_side = +1
                state.last_action_ts = now
                reason = (
                    f"mm comp={comp:.4f} skew5m_bps={skew_avg:.4f} imb={imb:.4f} "
                    f"tape_p={tape_p:.4f} hits_ba={feat.tape_bid_hit_count}/{feat.tape_ask_hit_count} "
                    f"mode={'fade' if self._fade else 'follow'}"
                )
                score = min(1.0, abs(comp) / entry)
                signal_log(logger, f"MM tilt -> BUY {symbol} qty={qty:.10f} ({reason})")
                signals.append(Signal(symbol=symbol, side=Side.BUY, qty=qty, reason=reason, score=score))
                continue

            if want_sell and state.open_side != -1:
                state.open_side = -1
                state.last_action_ts = now
                reason = (
                    f"mm comp={comp:.4f} skew5m_bps={skew_avg:.4f} imb={imb:.4f} "
                    f"tape_p={tape_p:.4f} hits_ba={feat.tape_bid_hit_count}/{feat.tape_ask_hit_count} "
                    f"mode={'fade' if self._fade else 'follow'}"
                )
                score = min(1.0, abs(comp) / entry)
                signal_log(logger, f"MM tilt -> SELL {symbol} qty={qty:.10f} ({reason})")
                signals.append(Signal(symbol=symbol, side=Side.SELL, qty=qty, reason=reason, score=score))

        return signals

    def _desired_sides(self, comp: float, entry: float) -> tuple[bool, bool]:
        """Return (want_buy, want_sell) from composite tilt and mode."""
        if self._fade:
            return comp <= -entry, comp >= entry
        return comp >= entry, comp <= -entry

    def _tape_pressure(self, feat: Features) -> float:
        """Net buyer vs seller aggression from print counts in [-1, 1].

        Positive => more offer lifts than bid hits in the tape window.
        """
        bid_n = int(feat.tape_bid_hit_count)
        ask_n = int(feat.tape_ask_hit_count)
        total = bid_n + ask_n
        min_tr = max(1, int(self._settings.mm_min_tape_trades))
        if total < min_tr:
            return 0.0
        return (ask_n - bid_n) / float(total)

    def _record_skew(self, state: _SymbolState, feat: Features, now: float) -> None:
        mid = feat.mid
        micro = feat.micro_price
        if mid is None or micro is None or mid <= 0 or micro <= 0:
            return
        skew_bps = (micro - mid) / mid * 10_000.0
        state.skew_samples.append((now, skew_bps))
        self._prune_skew(state, now)

    def _prune_skew(self, state: _SymbolState, now: float) -> None:
        cutoff = now - self._window
        dq = state.skew_samples
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _skew_mean(self, state: _SymbolState, now: float) -> float | None:
        self._prune_skew(state, now)
        dq = state.skew_samples
        min_samples = int(self._settings.mm_min_samples)
        if len(dq) < max(1, min_samples):
            return None
        return sum(v for _, v in dq) / len(dq)

    def _state_for(self, symbol: str) -> _SymbolState:
        st = self._state.get(symbol)
        if st is None:
            st = _SymbolState()
            self._state[symbol] = st
        return st

    def _size_for(self, mid: float) -> float:
        provider = self._equity_provider
        if provider is None:
            return float(self._settings.mm_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001
            return float(self._settings.mm_qty)
        if equity <= 0 or mid <= 0:
            return float(self._settings.mm_qty)
        risk_pct = float(self._settings.mm_risk_per_trade_pct)
        stop_pct = float(self._settings.default_stop_loss_pct)
        if risk_pct <= 0 or stop_pct <= 0:
            return float(self._settings.mm_qty)
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)
