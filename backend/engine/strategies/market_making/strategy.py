"""Fee-aware institutional market making (MM 2.0) — post-only quotes."""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import Settings
from common.types import QuoteIntent, Signal

from ...market_data.feature_store import Features
from ...market_data.own_quote_book import OwnBookState
from . import core as mm_core
from .calibrated import mm2_fee_edge_floor_bps, mm2_fee_round_trip_bps, mm_float
from .symbol_params import required_min_spread_bps
from .universe import resolve_mm2_symbols
from ..position_sync import VenuePosition, VenuePositionProvider
from ..strategy_base import StrategyBase

logger = logging.getLogger(__name__)

FillVwapProvider = Callable[[str], float]
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
    description = "Microstructure MM: two-sided liquidity or tape-confirmed one-sided mode"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm2_skew_window_sec)
        if self._window <= 0:
            raise ValueError("MM2_SKEW_WINDOW_SEC must be positive")
        self._symbols = resolve_mm2_symbols(settings)
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._own_provider: OwnBookProvider | None = None
        self._fill_vwap_provider: FillVwapProvider | None = None
        self._venue_position_provider = None
        self._gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_gate_log_ts: float = 0.0

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm2_skew_window_sec)
        self._symbols = resolve_mm2_symbols(settings)

    def manages_own_risk(self) -> bool:
        return bool(self._settings.mm_institutional_risk_enabled)

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def attach_own_book_provider(self, provider: OwnBookProvider) -> None:
        self._own_provider = provider

    def attach_fill_vwap_provider(self, provider: FillVwapProvider) -> None:
        self._fill_vwap_provider = provider

    def attach_venue_position_provider(self, provider: VenuePositionProvider) -> None:
        self._venue_position_provider = provider

    def _venue_position(self, symbol: str) -> VenuePosition | None:
        provider = self._venue_position_provider
        if provider is None:
            return None
        try:
            return provider(symbol)
        except Exception:  # noqa: BLE001
            return None

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        pass

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        return []

    def _open_position_count(self) -> int:
        n = 0
        for sym in self._symbols:
            if abs(self._position_qty(sym)) > 1e-8:
                n += 1
        return n

    def _total_inventory_notional(self, features: dict[str, Features]) -> float:
        total = 0.0
        for sym in self._symbols:
            qty = self._position_qty(sym)
            if abs(qty) < 1e-8:
                continue
            feat = features.get(sym)
            mid = float(feat.mid) if feat is not None and feat.mid is not None else 0.0
            if mid > 0:
                total += abs(qty) * mid
        return total

    def on_tick_quotes(self, features: dict[str, Features]) -> list[QuoteIntent]:
        equity = self._equity()
        intents: list[QuoteIntent] = []
        now = time.time()
        open_positions = self._open_position_count()
        max_concurrent = int(self._settings.mm2_max_concurrent_positions)
        total_inv_cap = float(self._settings.mm2_max_inventory_notional_total)
        total_inv_notional = (
            self._total_inventory_notional(features) if total_inv_cap > 0 else 0.0
        )
        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None:
                continue
            if not self._spread_ok(feat):
                intents.append(
                    self._suppressed_intent(symbol, "mm2_spread_gate", feat)
                )
                continue
            state = self._state_for(symbol)
            self._record_skew(state, feat, now)
            skew_avg = self._skew_mean(state, now)
            if skew_avg is None:
                if self._settings.mm2_quote_during_warmup:
                    skew_avg = 0.0
                else:
                    intents.append(
                        self._suppressed_intent(symbol, "mm2_skew_warmup", feat)
                    )
                    continue
            if not self._settings.mm2_two_sided_always:
                min_skew = mm_float(
                    symbol,
                    self._settings,
                    "mm2_min_skew_bps",
                    cal_attr="min_skew_bps",
                )
                if abs(skew_avg) < min_skew:
                    intents.append(
                        self._suppressed_intent(
                            symbol, "mm2_skew_gate", feat, skew_avg=skew_avg
                        )
                    )
                    continue
            own = self._own(symbol)
            pos_qty = self._position_qty(symbol)
            fill_entry = self._fill_entry(symbol, own)
            s = _Mm2SettingsAdapter(self._settings)
            mm_core.update_vol_regime_halt(own, feat, s, now=now)
            mm_core.update_consecutive_fill_halts(own, s, now=now)
            if (
                abs(pos_qty) < 1e-8
                and max_concurrent > 0
                and open_positions >= max_concurrent
            ):
                intents.append(
                    self._suppressed_intent(
                        symbol, "mm2_max_concurrent", feat, skew_avg=skew_avg
                    )
                )
                continue
            if (
                abs(pos_qty) < 1e-8
                and total_inv_cap > 0
                and total_inv_notional >= total_inv_cap
            ):
                intents.append(
                    self._suppressed_intent(
                        symbol, "mm2_inventory_total", feat, skew_avg=skew_avg
                    )
                )
                continue
            exit_reason = mm_core.plan_exit_reason(
                feat=feat,
                settings=s,
                own=own,
                position_qty=pos_qty,
                mid=float(feat.mid),
                venue=self._venue_position(symbol),
                fill_entry=fill_entry,
            )
            if exit_reason:
                intent = mm_core.build_exit_quote_intent(
                    feat=feat,
                    settings=s,
                    own=own,
                    position_qty=pos_qty,
                    reason=exit_reason,
                    strategy_name=self.name,
                    venue=self._venue_position(symbol),
                    fill_entry=fill_entry,
                )
                if intent is not None:
                    hold_sec = (
                        now - own.ledger.opened_ts if own.ledger.opened_ts > 0 else 0.0
                    )
                    intent.exit_hold_sec = hold_sec
                    self._stamp_obs(intent, feat, skew_avg=skew_avg)
                    self._log_exit(symbol, exit_reason, hold_sec, intent.unrealized_pnl_bps)
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
                fee_round_trip_bps=mm2_fee_round_trip_bps(symbol, self._settings),
                venue=self._venue_position(symbol),
                fill_entry=fill_entry,
            )
            tape_p: float | None = None
            if not self._settings.mm2_two_sided_always:
                tape_thr = float(self._settings.mm2_tape_confirm)
                if tape_thr > 0:
                    tape_p = mm_core.tape_pressure(feat, s)
                    if not _tape_confirms(skew_avg, tape_p, tape_thr):
                        intent.bid_price = None
                        intent.ask_price = None
                        intent.bid_qty = 0.0
                        intent.ask_qty = 0.0
                        intent.reason = f"{intent.reason} | mm2_tape_gate"
                        self._bump_gate(symbol, "mm2_tape_gate")
            if intent.reason.startswith("mm_entry_blocked"):
                self._bump_gate(symbol, "mm_entry_blocked")
            if not self._settings.mm2_two_sided_always:
                direction = mm_core.micro_direction(feat, s, skew_avg)
                mm_core.apply_asymmetric_quotes(
                    intent, direction=direction, position_qty=pos_qty
                )
            halted_bid = (
                intent.bid_price is not None and now < own.halt_bid_until
            )
            halted_ask = (
                intent.ask_price is not None and now < own.halt_ask_until
            )
            if halted_bid:
                intent.bid_price = None
                intent.bid_qty = 0.0
                self._bump_gate(symbol, "mm2_side_halt")
            if halted_ask:
                intent.ask_price = None
                intent.ask_qty = 0.0
                self._bump_gate(symbol, "mm2_side_halt")
            if halted_bid or halted_ask:
                intent.reason = f"{intent.reason} | mm2_side_halt"
            if now < own.vol_regime_halt_until:
                intent.bid_price = None
                intent.ask_price = None
                intent.bid_qty = 0.0
                intent.ask_qty = 0.0
                intent.reason = f"{intent.reason} | mm2_vol_regime"
                self._bump_gate(symbol, "mm2_vol_regime")
            self._stamp_obs(intent, feat, skew_avg=skew_avg, tape_p=tape_p)
            intents.append(intent)
        self._maybe_log_gate_summary(now)
        return intents

    def _spread_ok(self, feat: Features) -> bool:
        spread = feat.spread_bps
        if spread is None or spread <= 0:
            return False
        mode = (self._settings.mm2_spread_gate_mode or "standard").strip().lower()
        if mode == "off":
            return True
        if mode == "fee_floor":
            required = mm2_fee_edge_floor_bps(feat.symbol, self._settings)
            explicit = float(self._settings.mm2_min_spread_bps)
            if explicit > 0:
                required = max(required, explicit)
            return spread >= required
        calibrated_only = mode in ("calibrated", "calibration")
        required = required_min_spread_bps(
            feat.symbol,
            self._settings,
            feat,
            explicit_min_spread_bps=float(self._settings.mm2_min_spread_bps),
            explicit_min_edge_bps=float(self._settings.mm2_min_edge_bps),
            calibrated_only=calibrated_only,
        )
        return spread >= required

    def _own(self, symbol: str) -> OwnBookState:
        if self._own_provider is not None:
            return self._own_provider(symbol)
        return OwnBookState(symbol=symbol.upper())

    def _fill_entry(self, symbol: str, own: OwnBookState) -> float:
        provider = self._fill_vwap_provider
        if provider is not None:
            try:
                vwap = float(provider(symbol))
            except Exception:  # noqa: BLE001
                vwap = 0.0
            if vwap > 0:
                return vwap
        return own.ledger.entry_mid

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

    def _bump_gate(self, symbol: str, tag: str) -> None:
        self._gate_counts[symbol][tag] += 1

    def _maybe_log_gate_summary(self, now: float) -> None:
        interval = float(self._settings.mm2_scan_log_interval_sec)
        if not self._gate_counts:
            return
        if interval > 0 and self._last_gate_log_ts > 0 and now - self._last_gate_log_ts < interval:
            return
        self._last_gate_log_ts = now
        for sym, counts in self._gate_counts.items():
            if not counts:
                continue
            parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            logger.info(
                "MM2 gates %s last %.0fs: %s",
                sym,
                interval,
                parts,
            )
        self._gate_counts.clear()

    def _suppressed_intent(
        self,
        symbol: str,
        gate_tag: str,
        feat: Features,
        *,
        skew_avg: float | None = None,
    ) -> QuoteIntent:
        self._bump_gate(symbol, gate_tag)
        intent = QuoteIntent(
            symbol=symbol,
            bid_price=None,
            ask_price=None,
            bid_qty=0.0,
            ask_qty=0.0,
            reason=gate_tag,
            strategy_name=self.name,
            venue_mid=float(feat.mid or 0.0),
        )
        self._stamp_obs(intent, feat, skew_avg=skew_avg)
        return intent

    @staticmethod
    def _stamp_obs(
        intent: QuoteIntent,
        feat: Features,
        *,
        skew_avg: float | None = None,
        tape_p: float | None = None,
    ) -> None:
        if feat.spread_bps is not None:
            intent.spread_bps = float(feat.spread_bps)
        if skew_avg is not None:
            intent.skew_avg_bps = skew_avg
        if tape_p is not None:
            intent.tape_pressure = tape_p

    @staticmethod
    def _log_exit(
        symbol: str,
        exit_reason: str,
        hold_sec: float,
        pnl_bps: float,
    ) -> None:
        if exit_reason.startswith("mm_market_exit"):
            exit_type = "market"
        elif exit_reason.startswith("mm_profit_exit"):
            exit_type = "profit"
        elif exit_reason.startswith("mm_aggressive_exit"):
            exit_type = "aggressive"
        elif exit_reason.startswith("mm_inventory_exit"):
            exit_type = "inventory"
        else:
            exit_type = "other"
        logger.info(
            "MM2 exit %s type=%s hold_sec=%.1f pnl_bps=%.2f %s",
            symbol,
            exit_type,
            hold_sec,
            pnl_bps,
            exit_reason,
        )


_MM2_FIELD_MAP = {
    "mm_min_skew_bps": "mm2_min_skew_bps",
    "mm_tape_confirm": "mm2_tape_confirm",
    "mm_skew_scale": "mm2_skew_scale",
    "mm_imbalance_scale": "mm2_imbalance_scale",
    "mm_tape_scale": "mm2_tape_scale",
    "mm_min_tape_trades": "mm2_min_tape_trades",
    "mm_min_exit_profit_bps": "mm2_min_exit_profit_bps",
    "mm_max_hold_sec": "mm2_max_hold_sec",
    "mm_qty": "mm2_qty",
    "mm_market_exit_loss_bps": "mm2_market_exit_loss_bps",
    "mm_aggressive_exit_loss_bps": "mm2_aggressive_exit_loss_bps",
    "mm_exit_inside_touch_bps": "mm2_exit_inside_touch_bps",
    "mm_exit_stale_sec": "mm2_exit_stale_sec",
    "mm_exit_scratch_bps": "mm2_exit_scratch_bps",
    "mm_max_inventory_notional": "mm2_max_inventory_notional",
    "mm_max_inventory_notional_total": "mm2_max_inventory_notional_total",
    "mm_max_concurrent_positions": "mm2_max_concurrent_positions",
    "mm_risk_widen_multiplier": "mm2_risk_widen_multiplier",
    "mm_risk_size_damp": "mm2_risk_size_damp",
    "mm_max_consecutive_same_side_fills": "mm2_max_consecutive_same_side_fills",
    "mm_side_halt_sec": "mm2_side_halt_sec",
    "mm_vol_regime_spike_mult": "mm2_vol_regime_spike_mult",
    "mm_vol_regime_pause_sec": "mm2_vol_regime_pause_sec",
    "mm_exit_aggressive_bps": "mm2_exit_aggressive_bps",
    "mm_exit_loss_ramp_bps": "mm2_exit_loss_ramp_bps",
    "mm_exit_cross_touch": "mm2_exit_cross_touch",
    "mm_early_loss_hold_frac": "mm2_early_loss_hold_frac",
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


def _tape_confirms(skew_avg: float, tape_p: float, threshold: float) -> bool:
    if skew_avg <= 0 and tape_p <= -threshold:
        return True
    if skew_avg >= 0 and tape_p >= threshold:
        return True
    return False

