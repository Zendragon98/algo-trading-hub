"""Fee-aware microstructure fade (MM 2.0).

Same signal stack as ``market_making`` (rolling skew, L2 imbalance, tape
pressure) but tuned for post-commission viability:

* **Fade-only** — mean-reversion on composite extremes.
* **Spread / fee gate** — skip entries unless quoted spread covers a
  round-trip fee budget (maker vs taker from ``POST_ONLY_ENABLED``).
* **Tape confirmation** — aggressor flow must agree with the overshoot.
* **Skew floor** — require ``|skew_5m_bps| >= mm2_min_skew_bps``.
* **Profit + time exits** — take winners at ``mm2_min_exit_profit_bps``
  and force flat after ``mm2_max_hold_sec``.
* **Stricter defaults** — higher entry tilt, longer cooldown, one entry
  per tick.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

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
    skew_samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=4096)
    )
    open_side: int = 0
    last_action_ts: float = 0.0
    position_opened_ts: float = 0.0
    entry_mid: float = 0.0


@dataclass(slots=True)
class _ScanSnapshot:
    quoted: int = 0
    book_ready: int = 0
    skew_warming: int = 0
    spread_blocked: int = 0
    composite_weak: int = 0
    tape_blocked: int = 0
    skew_blocked: int = 0
    best_symbol: str = ""
    best_comp: float = 0.0
    best_spread_bps: float = 0.0


class MarketMakingV2Strategy(StrategyBase):
    name = "market_making_v2"
    display_label = "Market making 2.0 (fee-aware fade)"
    description = (
        "Skew + imbalance + tape fade with spread/fee gate, tape confirm, "
        "min-profit and time-stop exits"
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._window = float(settings.mm2_skew_window_sec)
        if self._window <= 0:
            raise ValueError("MM2_SKEW_WINDOW_SEC must be positive")
        self._symbols = self._resolve_universe(settings)
        self._state: dict[str, _SymbolState] = {}
        self._equity_provider: EquityProvider | None = None
        self._last_scan_log_ts: float = 0.0

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        w = float(settings.mm2_skew_window_sec)
        if w <= 0:
            raise ValueError("MM2_SKEW_WINDOW_SEC must be positive")
        self._window = w
        self._symbols = self._resolve_universe(settings)

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [
            s.strip().upper() for s in (settings.mm2_symbols or []) if s.strip()
        ]
        if configured:
            if len(configured) == 1 and configured[0] == "AUTO":
                return MarketMakingV2Strategy._engine_symbol_universe(settings)
            return sorted(set(configured))
        return MarketMakingV2Strategy._engine_symbol_universe(settings)

    @staticmethod
    def _engine_symbol_universe(settings: Settings) -> list[str]:
        syms = sorted(
            {str(s).strip().upper() for s in (settings.symbols or []) if str(s).strip()}
        )
        return syms if syms else ["BTCUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        state = self._state.get(symbol)
        if state is None:
            return
        prev_side = state.open_side
        self._sync_open_side_from_position(state, symbol)
        if state.open_side == 0:
            state.position_opened_ts = 0.0
            state.entry_mid = 0.0
        elif prev_side == 0 and state.open_side != 0 and state.entry_mid <= 0:
            state.position_opened_ts = time.time()

    def _exit_tilt(self) -> float:
        explicit = float(self._settings.mm2_exit_tilt or 0.0)
        if explicit > 0:
            return explicit
        entry = float(self._settings.mm2_entry_tilt)
        return max(entry * 0.35, 1.0)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.mm2_cooldown_sec)
        entry = float(self._settings.mm2_entry_tilt)
        exit_tilt = self._exit_tilt()
        if entry <= 0:
            return []
        signals: list[Signal] = []
        scan = _ScanSnapshot()

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None:
                continue
            if feat.mid is not None:
                scan.quoted += 1
            if feat.mid is None:
                continue

            state = self._state_for(symbol)
            self._sync_open_side_from_position(state, symbol)
            self._ensure_entry_anchor(state, feat, now)

            if now - state.last_action_ts < cooldown:
                self._record_skew(state, feat, now)
                continue

            self._record_skew(state, feat, now)
            skew_avg = self._skew_mean(state, now)
            if skew_avg is None:
                scan.skew_warming += 1

            imb = float(feat.imbalance_topn)
            tape_p = self._tape_pressure(feat)
            comp = 0.0
            if skew_avg is not None:
                comp = (
                    float(self._settings.mm2_skew_scale) * skew_avg
                    + float(self._settings.mm2_imbalance_scale) * imb
                    + float(self._settings.mm2_tape_scale) * tape_p
                )
                scan.book_ready += 1
                if abs(comp) > abs(scan.best_comp):
                    scan.best_comp = comp
                    scan.best_symbol = symbol
                    scan.best_spread_bps = float(feat.spread_bps or 0.0)

            entry_qty = self._size_for(feat.mid or 0.0)
            pos_qty = self._position_qty(symbol)
            actual = side_from_qty(pos_qty)
            mid = float(feat.mid)

            exit_reason = self._exit_reason(
                state=state,
                now=now,
                comp=comp,
                exit_tilt=exit_tilt,
                mid=mid,
                pos_qty=pos_qty,
                skew_avg=skew_avg if skew_avg is not None else 0.0,
                imb=imb,
                tape_p=tape_p,
            )
            if actual != 0 and exit_reason is not None:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=0,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open="mm2_entry",
                    reason_close=exit_reason,
                    score=min(1.0, abs(comp) / max(exit_tilt, 1e-9)),
                )
                if sig is not None:
                    state.last_action_ts = now
                    signal_log(
                        logger,
                        f"MM2 exit -> {sig.side.value.upper()} {symbol} qty={sig.qty:.10f}",
                    )
                    signals.append(sig)
                continue

            if entry_qty <= 0 or actual != 0:
                continue

            if skew_avg is None:
                continue

            block = self._entry_block_reason(feat, comp, entry, skew_avg, tape_p)
            if block is not None:
                if block == "spread":
                    scan.spread_blocked += 1
                elif block == "composite":
                    scan.composite_weak += 1
                elif block == "skew":
                    scan.skew_blocked += 1
                elif block == "tape":
                    scan.tape_blocked += 1
                continue

            want_buy = comp <= -entry
            want_sell = comp >= entry
            sig: Signal | None = None
            if want_buy:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=+1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=(
                        f"mm2 comp={comp:.4f} skew5m_bps={skew_avg:.4f} imb={imb:.4f} "
                        f"tape_p={tape_p:.4f} spread_bps={feat.spread_bps} "
                        f"hits_ba={feat.tape_bid_hit_count}/{feat.tape_ask_hit_count}"
                    ),
                    reason_close="mm2_entry_close",
                    score=min(1.0, abs(comp) / entry),
                )
            elif want_sell:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=-1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=(
                        f"mm2 comp={comp:.4f} skew5m_bps={skew_avg:.4f} imb={imb:.4f} "
                        f"tape_p={tape_p:.4f} spread_bps={feat.spread_bps} "
                        f"hits_ba={feat.tape_bid_hit_count}/{feat.tape_ask_hit_count}"
                    ),
                    reason_close="mm2_entry_close",
                    score=min(1.0, abs(comp) / entry),
                )

            if sig is None:
                continue
            state.last_action_ts = now
            state.position_opened_ts = now
            state.entry_mid = mid
            state.open_side = 1 if sig.side.value.lower() == "buy" else -1
            signal_log(
                logger,
                f"MM2 entry -> {sig.side.value.upper()} {symbol} qty={sig.qty:.10f}",
            )
            signals.append(sig)

        self._maybe_log_scan_heartbeat(now, scan, signal_count=len(signals))
        return self._cap_entries(signals)

    def _maybe_log_scan_heartbeat(
        self,
        now: float,
        scan: _ScanSnapshot,
        *,
        signal_count: int,
    ) -> None:
        interval = float(self._settings.mm2_scan_log_interval_sec)
        if interval <= 0:
            return
        if self._last_scan_log_ts > 0 and now - self._last_scan_log_ts < interval:
            return
        self._last_scan_log_ts = now
        fee_rt = self._fee_round_trip_bps()
        logger.info(
            "MM2 scan heartbeat: universe=%d quoted=%d ready=%d warming=%d "
            "blocked spread=%d composite=%d skew=%d tape=%d "
            "best=%s comp=%.3f spread_bps=%.2f fee_rt_bps=%.1f signals=%d",
            len(self._symbols),
            scan.quoted,
            scan.book_ready,
            scan.skew_warming,
            scan.spread_blocked,
            scan.composite_weak,
            scan.skew_blocked,
            scan.tape_blocked,
            scan.best_symbol or "-",
            scan.best_comp,
            scan.best_spread_bps,
            fee_rt,
            signal_count,
        )

    def _exit_reason(
        self,
        *,
        state: _SymbolState,
        now: float,
        comp: float,
        exit_tilt: float,
        mid: float,
        pos_qty: float,
        skew_avg: float,
        imb: float,
        tape_p: float,
    ) -> str | None:
        if side_from_qty(pos_qty) == 0:
            return None

        pnl_bps = self._unrealized_bps(mid, state.entry_mid, pos_qty)
        max_hold = float(self._settings.mm2_max_hold_sec)
        if max_hold > 0 and state.position_opened_ts > 0:
            held = now - state.position_opened_ts
            if held >= max_hold:
                return (
                    f"mm2_time_exit held={held:.1f}s pnl_bps={pnl_bps:.2f} "
                    f"comp={comp:.4f}"
                )

        min_profit = float(self._settings.mm2_min_exit_profit_bps)
        if min_profit > 0 and pnl_bps >= min_profit:
            return (
                f"mm2_profit_exit pnl_bps={pnl_bps:.2f} comp={comp:.4f} "
                f"skew5m_bps={skew_avg:.4f} imb={imb:.4f} tape_p={tape_p:.4f}"
            )

        if abs(comp) <= exit_tilt:
            return (
                f"mm2_signal_exit comp={comp:.4f} pnl_bps={pnl_bps:.2f} "
                f"skew5m_bps={skew_avg:.4f} imb={imb:.4f} tape_p={tape_p:.4f}"
            )
        return None

    def _entry_block_reason(
        self,
        feat: Features,
        comp: float,
        entry: float,
        skew_avg: float,
        tape_p: float,
    ) -> str | None:
        if abs(comp) < entry:
            return "composite"

        min_skew = float(self._settings.mm2_min_skew_bps)
        if min_skew > 0 and abs(skew_avg) < min_skew:
            return "skew"

        if not self._spread_covers_fees(feat, comp):
            return "spread"

        confirm = float(self._settings.mm2_tape_confirm)
        if confirm > 0 and not self._tape_confirms(comp, tape_p, confirm):
            return "tape"

        return None

    def _spread_covers_fees(self, feat: Features, comp: float) -> bool:
        """Optional explicit spread floors; auto mode does not block tight BBOs."""
        spread = feat.spread_bps
        if spread is None:
            return False

        floor = float(self._settings.mm2_min_spread_bps)
        if floor > 0:
            return spread >= floor

        min_edge = float(self._settings.mm2_min_edge_bps)
        if min_edge > 0:
            return spread >= min_edge

        fee_scale = float(self._settings.mm2_composite_fee_scale)
        if fee_scale > 0:
            cost = self._fee_round_trip_bps() + spread / 2.0 + float(
                self._settings.mm2_spread_buffer_bps
            )
            return abs(comp) >= float(self._settings.mm2_entry_tilt) + cost * fee_scale

        return True

    def _fee_round_trip_bps(self) -> float:
        explicit = float(self._settings.mm2_fee_round_trip_bps or 0.0)
        if explicit > 0:
            return explicit
        per_leg = (
            float(self._settings.mm2_maker_fee_bps)
            if self._settings.post_only_enabled
            else float(self._settings.mm2_taker_fee_bps)
        )
        return 2.0 * per_leg

    @staticmethod
    def _tape_confirms(comp: float, tape_p: float, threshold: float) -> bool:
        """Fade entries need tape pressure aligned with the overshoot."""
        if comp <= 0 and tape_p <= -threshold:
            return True
        if comp >= 0 and tape_p >= threshold:
            return True
        return False

    @staticmethod
    def _unrealized_bps(mid: float, entry_mid: float, pos_qty: float) -> float:
        if entry_mid <= 0 or mid <= 0 or abs(pos_qty) < 1e-12:
            return 0.0
        if pos_qty > 0:
            return (mid - entry_mid) / entry_mid * 10_000.0
        return (entry_mid - mid) / entry_mid * 10_000.0

    def _ensure_entry_anchor(self, state: _SymbolState, feat: Features, now: float) -> None:
        if state.open_side == 0:
            return
        if state.entry_mid <= 0 and feat.mid is not None and feat.mid > 0:
            state.entry_mid = float(feat.mid)
        if state.position_opened_ts <= 0:
            state.position_opened_ts = now

    def _cap_entries(self, signals: list[Signal]) -> list[Signal]:
        max_n = int(self._settings.mm2_max_entries_per_tick or 0)
        if max_n <= 0:
            return signals
        exits = [s for s in signals if s.reduce_only]
        entries = [s for s in signals if not s.reduce_only]
        if len(entries) <= max_n:
            return signals
        entries.sort(key=lambda s: -float(s.score))
        return exits + entries[:max_n]

    def _tape_pressure(self, feat: Features) -> float:
        bid_n = int(feat.tape_bid_hit_count)
        ask_n = int(feat.tape_ask_hit_count)
        total = bid_n + ask_n
        min_tr = max(1, int(self._settings.mm2_min_tape_trades))
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
        min_samples = int(self._settings.mm2_min_samples)
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
            return float(self._settings.mm2_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001
            return float(self._settings.mm2_qty)
        if equity <= 0 or mid <= 0:
            return float(self._settings.mm2_qty)
        risk_pct = float(self._settings.mm2_risk_per_trade_pct)
        stop_pct = float(self._settings.default_stop_loss_pct)
        if risk_pct <= 0 or stop_pct <= 0:
            return float(self._settings.mm2_qty)
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)
