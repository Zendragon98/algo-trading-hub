"""Tape-flow momentum: follow sustained one-sided aggression.

When the public tape keeps lifting offers (buyers aggressive) or hitting
bids (sellers aggressive), market makers get run over. This strategy
enters *with* that flow across the MM scan universe instead of fading it.

Entries require tape pressure and book imbalance aligned for several
consecutive ticks. Exits use in-strategy risk (stop, trailing profit,
max hold, tape reversal) — see ``flow_pnl`` for entry/PnL semantics.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from common.config import Settings
from common.logging import signal_log_emit
from common.types import Signal
from common.universe_bootstrap import is_auto_symbol_list

from ..market_data.feature_store import Features
from .flow_pnl import (
    apply_attributed_fill_vwap,
    compute_flow_pnl,
    maybe_log_pnl_verification,
)
from .market_making.core import signed_tape_pressure
from .position_sync import (
    VenuePosition,
    VenuePositionProvider,
    plan_directional_signal,
    side_from_qty,
)
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]


@dataclass(slots=True)
class _SymbolState:
    confirm: deque[int] = field(default_factory=deque)
    signal_mid: float = 0.0
    fill_vwap: float = 0.0
    fill_qty_abs: float = 0.0
    entry_ts: float = 0.0
    open_side: int = 0
    last_action_ts: float = 0.0
    peak_pnl_bps: float = 0.0
    last_pnl_verify_ts: float = 0.0


def _tape_pressure(feat: Features, settings: Settings) -> float:
    return signed_tape_pressure(
        feat,
        mode=settings.flow_tape_mode or "volume",
        min_tape_trades=int(settings.flow_min_tape_trades),
    )


def _flow_direction(
    tape: float,
    imb: float,
    *,
    tape_thr: float,
    imb_min: float,
    depletion_asym: float,
    require_depletion: bool,
) -> int:
    if tape >= tape_thr and imb >= imb_min:
        if require_depletion and depletion_asym <= 0:
            return 0
        return 1
    if tape <= -tape_thr and imb <= -imb_min:
        if require_depletion and depletion_asym >= 0:
            return 0
        return -1
    return 0


class FlowMomentumStrategy(StrategyBase):
    name = "flow_momentum"
    display_label = "Flow momentum"
    description = "Follow sustained one-sided tape aggression across the MM scan universe"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._venue_position_provider: VenuePositionProvider | None = None
        self._last_scan_log_ts: float = 0.0

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [s.strip().upper() for s in (settings.flow_symbols or []) if s.strip()]
        if configured and not is_auto_symbol_list(configured):
            return sorted(set(configured))
        if configured and is_auto_symbol_list(configured):
            logger.warning(
                "FLOW_SYMBOLS=AUTO not expanded yet; restart after Binance universe bootstrap "
                "or set FLOW_SYMBOLS explicitly"
            )
        pins = [s.strip().upper() for s in (settings.mm_auto_pin_symbols or []) if s.strip()]
        return pins or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

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

    def manages_own_risk(self) -> bool:
        return True

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)

    def on_fill(
        self,
        symbol: str,
        qty: float,
        side: str,
        *,
        price: float | None = None,
    ) -> None:
        state = self._state.get(symbol)
        if state is None:
            return
        pos_side = side_from_qty(self._position_qty(symbol))
        if pos_side != state.open_side and pos_side != 0:
            state.fill_vwap = 0.0
            state.fill_qty_abs = 0.0
            state.peak_pnl_bps = 0.0
        state.open_side = pos_side
        if pos_side == 0:
            state.signal_mid = 0.0
            state.fill_vwap = 0.0
            state.fill_qty_abs = 0.0
            state.entry_ts = 0.0
            state.peak_pnl_bps = 0.0
            return
        if price is not None and price > 0 and qty > 0:
            state.fill_vwap, state.fill_qty_abs = apply_attributed_fill_vwap(
                fill_vwap=state.fill_vwap,
                fill_qty_abs=state.fill_qty_abs,
                fill_price=price,
                fill_qty=qty,
            )
        if state.entry_ts <= 0:
            state.entry_ts = time.time()

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        s = self._settings
        tape_thr = float(s.flow_tape_threshold)
        imb_min = float(s.flow_imbalance_min)
        confirm_n = max(1, int(s.flow_confirm_ticks))
        cooldown = float(s.flow_cooldown_sec)
        exit_tape = float(s.flow_exit_tape_threshold)
        require_dep = bool(s.flow_require_depletion)
        signals: list[Signal] = []
        quoted = warming = in_pos = entries = exits = 0

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None or feat.mid <= 0:
                continue
            mid = float(feat.mid)
            if mid < float(s.flow_min_mid_price):
                continue
            quoted += 1
            state = self._state_for(symbol, confirm_n)
            self._sync_state(state, symbol, mid, now)

            tape = _tape_pressure(feat, s)
            imb = float(feat.imbalance_topn)
            dep = float(feat.depth_depletion_asym)
            direction = _flow_direction(
                tape,
                imb,
                tape_thr=tape_thr,
                imb_min=imb_min,
                depletion_asym=dep,
                require_depletion=require_dep,
            )
            state.confirm.append(direction)
            while len(state.confirm) > confirm_n:
                state.confirm.popleft()

            pos_qty = self._position_qty(symbol)
            pos_side = side_from_qty(pos_qty)

            exit_sig = self._maybe_exit(
                symbol=symbol,
                state=state,
                pos_side=pos_side,
                pos_qty=pos_qty,
                mid=mid,
                tape=tape,
                now=now,
                exit_tape=exit_tape,
            )
            if exit_sig is not None:
                exits += 1
                state.last_action_ts = now
                signals.append(exit_sig)
                continue

            if pos_side != 0:
                in_pos += 1
                continue

            if now - state.last_action_ts < cooldown:
                continue
            if len(state.confirm) < confirm_n:
                warming += 1
                continue
            if not all(d == direction and d != 0 for d in state.confirm):
                continue

            min_vel = float(s.flow_min_tape_velocity)
            if min_vel > 0 and float(feat.tape_velocity) < min_vel:
                continue
            if bool(s.flow_skip_toxic) and feat.is_toxic:
                continue

            entry_qty = self._size_for(mid)
            if entry_qty <= 0:
                continue

            score = min(
                1.0,
                float(s.flow_entry_score)
                * (0.5 + 0.5 * min(abs(tape) / max(tape_thr, 1e-9), 2.0)),
            )
            sig = plan_directional_signal(
                symbol=symbol,
                target_side=direction,
                entry_qty=entry_qty,
                position_qty=pos_qty,
                reason_open=(
                    f"flow_momentum_enter tape={tape:+.3f} imb={imb:+.3f} "
                    f"dep={dep:+.3f} n={confirm_n}"
                ),
                reason_close="flow_momentum_flatten",
                score=score,
            )
            if sig is None or sig.reduce_only:
                continue
            entries += 1
            state.last_action_ts = now
            state.signal_mid = mid
            state.entry_ts = now
            state.open_side = direction
            state.peak_pnl_bps = 0.0
            signal_log_emit(
                logger,
                f"FLOW open {sig.side.value.upper()} {symbol} qty={sig.qty:.8f} "
                f"tape={tape:+.3f} entry_mid={mid:.6f}(signal_mid provisional)",
                reason=sig.reason,
            )
            signals.append(sig)

        signals = self._cap_entries(signals)
        self._maybe_log_scan(
            now=now,
            quoted=quoted,
            warming=warming,
            in_pos=in_pos,
            entries=entries,
            exits=exits,
            signal_count=len(signals),
        )
        return signals

    def _maybe_exit(
        self,
        *,
        symbol: str,
        state: _SymbolState,
        pos_side: int,
        pos_qty: float,
        mid: float,
        tape: float,
        now: float,
        exit_tape: float,
    ) -> Signal | None:
        if pos_side == 0:
            return None

        venue = self._venue_position(symbol)
        snap = compute_flow_pnl(
            pos_side=pos_side,
            pos_qty=pos_qty,
            mid=mid,
            fill_vwap=state.fill_vwap,
            venue=venue,
        )
        state.last_pnl_verify_ts = maybe_log_pnl_verification(
            symbol=symbol,
            snap=snap,
            pos_qty=pos_qty,
            pos_side=pos_side,
            now=now,
            last_log_ts=state.last_pnl_verify_ts,
            log_interval_sec=float(self._settings.flow_pnl_verify_log_interval_sec),
            max_drift_bps=float(self._settings.flow_pnl_verify_max_drift_bps),
        )

        pnl = snap.exit_bps
        hold = now - state.entry_ts if state.entry_ts > 0 else 0.0
        s = self._settings
        tp = float(s.flow_take_profit_bps)
        sl = float(s.flow_stop_loss_bps)
        max_hold = float(s.flow_max_hold_sec)
        trail_stop = float(s.flow_trail_stop_bps)
        trail_arm = float(s.flow_trail_arm_bps) or tp
        pnl_known = snap.entry_source != "unknown"

        if pnl_known and pnl > state.peak_pnl_bps:
            state.peak_pnl_bps = pnl

        reason: str | None = None
        if pnl_known and sl > 0 and pnl <= -sl:
            reason = f"flow_stop_loss pnl_bps={pnl:.2f} entry={snap.entry_source}"
        elif (
            pnl_known
            and trail_stop > 0
            and state.peak_pnl_bps >= trail_arm
            and pnl <= state.peak_pnl_bps - trail_stop
        ):
            reason = (
                f"flow_trail_stop peak={state.peak_pnl_bps:.2f} "
                f"pnl_bps={pnl:.2f} entry={snap.entry_source}"
            )
        elif pnl_known and trail_stop <= 0 and tp > 0 and pnl >= tp:
            reason = f"flow_take_profit pnl_bps={pnl:.2f} entry={snap.entry_source}"
        elif max_hold > 0 and hold >= max_hold:
            reason = (
                f"flow_max_hold pnl_bps={pnl:.2f} hold_sec={hold:.1f} "
                f"entry={snap.entry_source}"
            )
        elif pos_side > 0 and tape <= -exit_tape:
            reason = f"flow_reversal tape={tape:+.3f} pnl_bps={pnl:.2f}"
        elif pos_side < 0 and tape >= exit_tape:
            reason = f"flow_reversal tape={tape:+.3f} pnl_bps={pnl:.2f}"

        if reason is None:
            return None
        sig = plan_directional_signal(
            symbol=symbol,
            target_side=0,
            entry_qty=0.0,
            position_qty=pos_qty,
            reason_open="",
            reason_close=reason,
            score=float(s.flow_entry_score),
        )
        if sig is None:
            return None
        signal_log_emit(
            logger,
            f"FLOW close {symbol} {reason} "
            f"entry_px={snap.entry_price:.6f} verified={snap.verified}",
            reason=reason,
        )
        return sig

    def _sync_state(self, state: _SymbolState, symbol: str, mid: float, now: float) -> None:
        pos_side = side_from_qty(self._position_qty(symbol))
        if pos_side != state.open_side:
            if pos_side == 0:
                state.signal_mid = 0.0
                state.fill_vwap = 0.0
                state.fill_qty_abs = 0.0
                state.entry_ts = 0.0
                state.peak_pnl_bps = 0.0
            else:
                state.peak_pnl_bps = 0.0
                if state.entry_ts <= 0:
                    state.entry_ts = now
            state.open_side = pos_side

    def _state_for(self, symbol: str, confirm_n: int) -> _SymbolState:
        state = self._state.get(symbol)
        if state is None:
            state = _SymbolState(confirm=deque(maxlen=max(confirm_n, 1)))
            self._state[symbol] = state
        elif state.confirm.maxlen != confirm_n:
            state.confirm = deque(state.confirm, maxlen=max(confirm_n, 1))
        return state

    def _size_for(self, mid: float) -> float:
        provider = self._equity_provider
        if provider is None:
            return float(self._settings.flow_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001
            return float(self._settings.flow_qty)
        if equity <= 0:
            return float(self._settings.flow_qty)
        universe_n = max(1, len(self._symbols))
        risk_pct = float(self._settings.flow_risk_per_trade_pct) / universe_n
        stop_bps = float(self._settings.flow_stop_loss_bps)
        if risk_pct <= 0 or stop_bps <= 0 or mid <= 0:
            return float(self._settings.flow_qty)
        stop_pct = stop_bps / 10_000.0
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)

    def _cap_entries(self, signals: list[Signal]) -> list[Signal]:
        max_n = int(getattr(self._settings, "flow_max_entries_per_tick", 0) or 0)
        if max_n <= 0:
            return signals
        exits = [sig for sig in signals if sig.reduce_only]
        entries = [sig for sig in signals if not sig.reduce_only]
        if len(entries) <= max_n:
            return signals
        entries.sort(key=lambda sig: -float(sig.score))
        return exits + entries[:max_n]

    def _maybe_log_scan(
        self,
        *,
        now: float,
        quoted: int,
        warming: int,
        in_pos: int,
        entries: int,
        exits: int,
        signal_count: int,
    ) -> None:
        interval = float(self._settings.flow_scan_log_interval_sec)
        if interval <= 0:
            return
        if self._last_scan_log_ts > 0 and now - self._last_scan_log_ts < interval:
            return
        self._last_scan_log_ts = now
        logger.info(
            "FLOW scan: universe=%d quoted=%d warming=%d in_pos=%d "
            "entries=%d exits=%d signals=%d thr=%.3f confirm=%d "
            "trail_arm=%.1f trail_stop=%.1f",
            len(self._symbols),
            quoted,
            warming,
            in_pos,
            entries,
            exits,
            signal_count,
            float(self._settings.flow_tape_threshold),
            int(self._settings.flow_confirm_ticks),
            float(self._settings.flow_trail_arm_bps or self._settings.flow_take_profit_bps),
            float(self._settings.flow_trail_stop_bps),
        )
