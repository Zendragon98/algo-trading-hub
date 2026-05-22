"""Fee-aware institutional market making (MM 2.0) — post-only quotes."""

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
from .market_making import MarketMakingStrategy
from .mm_symbol_params import resolve_mm_params
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]
OwnBookProvider = Callable[[str], OwnBookState]


@dataclass(slots=True)
class _SymbolState:
    skew_samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=4096)
    )


class MarketMakingV2Strategy(StrategyBase):
    name = "market_making_v2"
    display_label = "Market making 2.0 (quotes + fees)"
    description = "Quote MM with spread/fee gate, tape confirm, and profit exits"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm2_skew_window_sec)
        if self._window <= 0:
            raise ValueError("MM2_SKEW_WINDOW_SEC must be positive")
        self._symbols = self._resolve_universe(settings)
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._own_provider: OwnBookProvider | None = None

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm2_skew_window_sec)
        self._symbols = self._resolve_universe(settings)

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [
            s.strip().upper() for s in (settings.mm2_symbols or []) if s.strip()
        ]
        if configured:
            if len(configured) == 1 and configured[0] == "AUTO":
                return MarketMakingStrategy._engine_symbol_universe(settings)
            return sorted(set(configured))
        return MarketMakingStrategy._engine_symbol_universe(settings)

    def manages_own_risk(self) -> bool:
        return bool(self._settings.mm_institutional_risk_enabled)

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
            if not self._spread_ok(feat):
                intents.append(
                    QuoteIntent(
                        symbol=symbol,
                        bid_price=None,
                        ask_price=None,
                        bid_qty=0.0,
                        ask_qty=0.0,
                        reason="mm2_spread_gate",
                        strategy_name=self.name,
                    )
                )
                continue
            state = self._state_for(symbol)
            now = time.time()
            self._record_skew(state, feat, now)
            skew_avg = self._skew_mean(state, now)
            if skew_avg is not None and abs(skew_avg) < float(self._settings.mm2_min_skew_bps):
                intents.append(
                    QuoteIntent(
                        symbol=symbol,
                        bid_price=None,
                        ask_price=None,
                        bid_qty=0.0,
                        ask_qty=0.0,
                        reason="mm2_skew_gate",
                        strategy_name=self.name,
                    )
                )
                continue
            own = self._own(symbol)
            pos_qty = self._position_qty(symbol)
            s = _Mm2SettingsAdapter(self._settings)
            exit_reason = mm_core.plan_exit_reason(
                feat=feat,
                settings=s,
                own=own,
                position_qty=pos_qty,
                mid=float(feat.mid),
            )
            if exit_reason:
                intent = _exit_intent(feat, pos_qty, exit_reason, self.name, self._settings)
                if intent is not None:
                    intents.append(intent)
                    continue
            intent = mm_core.compute_quote_intent(
                feat=feat,
                settings=s,
                own=own,
                position_qty=pos_qty,
                equity=equity,
                skew_avg=skew_avg,
                strategy_name=self.name,
                fee_round_trip_bps=_fee_rt(self._settings),
            )
            if float(self._settings.mm2_tape_confirm) > 0:
                tape_p = mm_core.tape_pressure(feat, s)
                if not _tape_confirms(skew_avg or 0.0, tape_p, self._settings.mm2_tape_confirm):
                    intent.bid_price = None
                    intent.ask_price = None
            intents.append(intent)
        return intents

    def _spread_ok(self, feat: Features) -> bool:
        spread = feat.spread_bps
        if spread is None:
            return False
        params = resolve_mm_params(feat.symbol, self._settings, feat)
        floor = params.min_spread_bps
        if floor is None:
            floor = float(self._settings.mm2_min_spread_bps)
        if floor > 0:
            return spread >= floor
        min_edge = float(self._settings.mm2_min_edge_bps)
        if min_edge > 0:
            return spread >= min_edge
        return spread >= _fee_rt(self._settings) + float(self._settings.mm2_spread_buffer_bps)

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
        state.skew_samples.append((now, (micro - mid) / mid * 10_000.0))
        cutoff = now - self._window
        while state.skew_samples and state.skew_samples[0][0] < cutoff:
            state.skew_samples.popleft()

    def _skew_mean(self, state: _SymbolState, now: float) -> float | None:
        if len(state.skew_samples) < max(1, int(self._settings.mm2_min_samples)):
            return None
        return sum(v for _, v in state.skew_samples) / len(state.skew_samples)

    def _state_for(self, symbol: str) -> _SymbolState:
        st = self._state.get(symbol)
        if st is None:
            st = _SymbolState()
            self._state[symbol] = st
        return st


_MM2_FIELD_MAP = {
    "mm_skew_scale": "mm2_skew_scale",
    "mm_imbalance_scale": "mm2_imbalance_scale",
    "mm_tape_scale": "mm2_tape_scale",
    "mm_min_tape_trades": "mm2_min_tape_trades",
    "mm_min_exit_profit_bps": "mm2_min_exit_profit_bps",
    "mm_max_hold_sec": "mm2_max_hold_sec",
    "mm_qty": "mm2_qty",
}


class _Mm2SettingsAdapter:
    """Map mm2_* fields onto mm_* names for mm_core."""

    def __init__(self, s: Settings) -> None:
        self._s = s

    def __getattr__(self, name: str) -> object:
        alt = _MM2_FIELD_MAP.get(name)
        if alt is not None:
            return getattr(self._s, alt)
        return getattr(self._s, name)


def _fee_rt(settings: Settings) -> float:
    explicit = float(settings.mm2_fee_round_trip_bps or 0.0)
    if explicit > 0:
        return explicit
    per_leg = (
        float(settings.mm2_maker_fee_bps)
        if settings.post_only_enabled
        else float(settings.mm2_taker_fee_bps)
    )
    return 2.0 * per_leg


def _tape_confirms(skew_avg: float, tape_p: float, threshold: float) -> bool:
    if skew_avg <= 0 and tape_p <= -threshold:
        return True
    if skew_avg >= 0 and tape_p >= threshold:
        return True
    return False


def _exit_intent(
    feat: Features,
    pos_qty: float,
    reason: str,
    strategy_name: str,
    settings: Settings,
) -> QuoteIntent | None:
    mid = feat.mid or 0.0
    qty = abs(pos_qty)
    if mid <= 0 or qty <= 0:
        return None
    scratch_bps = mm_core.mm_float(feat.symbol, settings, "mm_exit_scratch_bps")
    if pos_qty > 0:
        return QuoteIntent(
            symbol=feat.symbol,
            bid_price=None,
            ask_price=mm_core.exit_pegged_price(mid, scratch_bps=scratch_bps, reduce_long=True),
            bid_qty=0.0,
            ask_qty=qty,
            reason=reason,
            strategy_name=strategy_name,
            reduce_only_ask=True,
        )
    return QuoteIntent(
        symbol=feat.symbol,
        bid_price=mm_core.exit_pegged_price(mid, scratch_bps=scratch_bps, reduce_long=False),
        ask_price=None,
        bid_qty=qty,
        ask_qty=0.0,
        reason=reason,
        strategy_name=strategy_name,
        reduce_only_bid=True,
    )
