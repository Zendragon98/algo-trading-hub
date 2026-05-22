"""Institutional market making — two-sided post-only quotes via QuoteExecutor."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import Settings
from common.types import QuoteIntent, Signal

from ..market_data.feature_store import Features
from ..market_data.own_quote_book import OwnBookState
from . import mm_core
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]
OwnBookProvider = Callable[[str], OwnBookState]


@dataclass(slots=True)
class _SymbolState:
    skew_samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=4096)
    )


class MarketMakingStrategy(StrategyBase):
    name = "market_making"
    display_label = "Market making (quotes)"
    description = (
        "Two-sided post-only quotes with skew, imbalance, tape, depletion, "
        "inventory and toxicity controls"
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm_skew_window_sec)
        if self._window <= 0:
            raise ValueError("MM_SKEW_WINDOW_SEC must be positive")
        self._symbols = self._resolve_universe(settings)
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._own_provider: OwnBookProvider | None = None

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        w = float(settings.mm_skew_window_sec)
        if w <= 0:
            raise ValueError("MM_SKEW_WINDOW_SEC must be positive")
        self._window = w
        self._symbols = self._resolve_universe(settings)

    def manages_own_risk(self) -> bool:
        return bool(self._settings.mm_institutional_risk_enabled)

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [s.strip().upper() for s in (settings.mm_symbols or []) if s.strip()]
        if configured:
            if len(configured) == 1 and configured[0] == "AUTO":
                return MarketMakingStrategy._engine_symbol_universe(settings)
            return sorted(set(configured))
        return MarketMakingStrategy._engine_symbol_universe(settings)

    @staticmethod
    def _engine_symbol_universe(settings: Settings) -> list[str]:
        syms = sorted(
            {str(s).strip().upper() for s in (settings.symbols or []) if str(s).strip()}
        )
        return syms if syms else ["BTCUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def attach_own_book_provider(self, provider: OwnBookProvider) -> None:
        self._own_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        pass

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        return []

    def on_tick_quotes(self, features: dict[str, Features]) -> list[QuoteIntent]:
        equity = self._equity()
        intents: list[QuoteIntent] = []
        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None:
                continue
            state = self._state_for(symbol)
            self._record_skew(state, feat, time.time())
            skew_avg = self._skew_mean(state, time.time())
            own = self._own(symbol)
            pos_qty = self._position_qty(symbol)
            exit_reason = mm_core.plan_exit_reason(
                feat=feat,
                settings=self._settings,
                own=own,
                position_qty=pos_qty,
                mid=float(feat.mid),
            )
            if exit_reason:
                intent = self._exit_quote_intent(feat, own, pos_qty, exit_reason)
                if intent is not None:
                    intents.append(intent)
                    continue
            intents.append(
                mm_core.compute_quote_intent(
                    feat=feat,
                    settings=self._settings,
                    own=own,
                    position_qty=pos_qty,
                    equity=equity,
                    skew_avg=skew_avg,
                    strategy_name=self.name,
                )
            )
        return intents

    def _exit_quote_intent(
        self,
        feat: Features,
        own: OwnBookState,
        pos_qty: float,
        reason: str,
    ) -> QuoteIntent | None:
        mid = feat.mid or 0.0
        if mid <= 0:
            return None
        qty = abs(pos_qty)
        if qty <= 0:
            return None
        scratch_bps = mm_core.mm_float(feat.symbol, self._settings, "mm_exit_scratch_bps")
        if pos_qty > 0:
            return QuoteIntent(
                symbol=feat.symbol,
                bid_price=None,
                ask_price=mm_core.exit_pegged_price(mid, scratch_bps=scratch_bps, reduce_long=True),
                bid_qty=0.0,
                ask_qty=qty,
                reason=reason,
                strategy_name=self.name,
                reduce_only_ask=True,
            )
        return QuoteIntent(
            symbol=feat.symbol,
            bid_price=mm_core.exit_pegged_price(mid, scratch_bps=scratch_bps, reduce_long=False),
            ask_price=None,
            bid_qty=qty,
            ask_qty=0.0,
            reason=reason,
            strategy_name=self.name,
            reduce_only_bid=True,
        )

    def _own(self, symbol: str) -> OwnBookState:
        if self._own_provider is not None:
            return self._own_provider(symbol)
        return OwnBookState(symbol=symbol.upper())

    def _equity(self) -> float:
        if self._equity_provider is None:
            return 0.0
        try:
            return float(self._equity_provider())
        except Exception:  # noqa: BLE001
            return 0.0

    def _record_skew(self, state: _SymbolState, feat: Features, now: float) -> None:
        mid = feat.mid
        micro = feat.micro_price
        if mid is None or micro is None or mid <= 0 or micro <= 0:
            return
        skew_bps = (micro - mid) / mid * 10_000.0
        state.skew_samples.append((now, skew_bps))
        cutoff = now - self._window
        dq = state.skew_samples
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _skew_mean(self, state: _SymbolState, now: float) -> float | None:
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
