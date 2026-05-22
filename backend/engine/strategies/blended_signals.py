"""Multi-indicator blended crypto strategy.

Combines five signal families common on Binance/crypto desks:

1. **EMA trend** — fast vs slow EMA (golden/death cross bias)
2. **MACD momentum** — MACD line vs signal line
3. **RSI filter** — momentum zone votes; blocks entries at extremes
4. **Bollinger %B** — mean-reversion at band extremes
5. **Microstructure** — order-book imbalance + trade-tape pressure

Each family emits a directional vote in ``{-1, 0, +1}``. Votes are
weighted-averaged into a blend score in ``[-1, +1]``. Entries fire on
blend threshold crosses (edge-triggered) so the strategy does not churn
every tick. Exits use a lower ``blend_exit`` band or an opposing cross.

Samples are mid prices, optionally aggregated into closed bars via
``blend_bar_interval_sec`` (same pattern as SMA crossover).
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

from ..market_data.feature_store import Features
from .indicators import bollinger_bands, ema_step, macd_step, rsi_from_closes
from .position_sync import plan_directional_signal, side_from_qty
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]


@dataclass(slots=True)
class _IndicatorState:
    """Per-symbol rolling indicator memory."""

    closes: deque[float] = field(default_factory=deque)
    last_close_in_bar: float = 0.0
    bar_bucket: int | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_macd_fast: float | None = None
    ema_macd_slow: float | None = None
    macd_signal: float | None = None
    prev_blend: float | None = None
    last_action_ts: float = 0.0


class BlendedSignalsStrategy(StrategyBase):
    name = "blended_signals"
    display_label = "Blended Signals"
    description = (
        "EMA + MACD + RSI + Bollinger + microstructure weighted blend for crypto"
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)
        self._bar_interval_sec = float(settings.blend_bar_interval_sec or 0.0)
        if self._bar_interval_sec < 0:
            raise ValueError("BLEND_BAR_INTERVAL_SEC must be >= 0")
        self._state: dict[str, _IndicatorState] = {}
        self._equity_provider: EquityProvider | None = None
        self._last_scan_log_ts: float = 0.0
        self._validate_windows(settings)

    @staticmethod
    def _validate_windows(settings: Settings) -> None:
        if settings.blend_ema_fast >= settings.blend_ema_slow:
            raise ValueError("BLEND_EMA_FAST must be < BLEND_EMA_SLOW")
        if settings.blend_macd_fast >= settings.blend_macd_slow:
            raise ValueError("BLEND_MACD_FAST must be < BLEND_MACD_SLOW")

    @staticmethod
    def _resolve_universe(settings: Settings) -> list[str]:
        configured = [
            s.strip().upper() for s in (settings.blend_symbols or []) if s.strip()
        ]
        if configured:
            return sorted(set(configured))
        legacy = (settings.blend_symbol or "").strip().upper()
        if legacy:
            return [legacy]
        return ["BTCUSDT", "ETHUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)
        self._bar_interval_sec = float(settings.blend_bar_interval_sec or 0.0)
        self._validate_windows(settings)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        # Position for entries/exits comes from attach_position_provider each tick;
        # log fills for audit only (no local open_side — unlike SMA).
        logger.debug("blend on_fill %s %s qty=%.8f", symbol, side, qty)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.blend_cooldown_sec)
        signals: list[Signal] = []
        quoted = warming = ready = 0
        best_symbol = ""
        best_blend = 0.0
        best_bull = 0
        best_bear = 0

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None or feat.mid <= 0:
                continue
            mid = float(feat.mid)
            if mid < float(self._settings.blend_min_mid_price):
                continue
            quoted += 1
            state = self._state_for(symbol)

            if now - state.last_action_ts < cooldown:
                self._push_sample(state, mid, now)
                continue

            if not self._push_sample(state, mid, now):
                continue
            min_bars = self._min_bars_required()
            if len(state.closes) < min_bars:
                warming += 1
                continue
            ready += 1

            close = state.closes[-1]
            votes = self._component_votes(state, close, feat)
            blend, score = self._blend_score(votes)
            if blend is None:
                continue

            bull_votes = int(votes.get("bull_votes", 0))
            bear_votes = int(votes.get("bear_votes", 0))
            if abs(blend) >= abs(best_blend):
                best_symbol = symbol
                best_blend = blend
                best_bull = bull_votes
                best_bear = bear_votes

            pos_qty = self._position_qty(symbol)
            pos_side = side_from_qty(pos_qty)
            entry_thresh = float(self._settings.blend_entry_threshold)
            exit_thresh = float(self._settings.blend_exit_threshold)

            sig: Signal | None = None
            prev = state.prev_blend
            state.prev_blend = blend

            if prev is None:
                continue

            entry_qty = self._size_for(mid)
            if entry_qty <= 0:
                logger.debug("BLEND %s skip: entry_qty<=0 blend=%.3f", symbol, blend)
                continue

            # Exit when blend weakens while positioned.
            if pos_side == 1 and blend < exit_thresh:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=0,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open="blend_long",
                    reason_close=f"blend_exit_long blend={blend:.3f}",
                    score=score,
                )
            elif pos_side == -1 and blend > -exit_thresh:
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=0,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open="blend_short",
                    reason_close=f"blend_exit_short blend={blend:.3f}",
                    score=score,
                )
            elif pos_side == 0:
                rsi_block_long = votes.get("rsi_block_long", False)
                rsi_block_short = votes.get("rsi_block_short", False)
                crossed_long = prev < entry_thresh <= blend
                crossed_short = prev > -entry_thresh >= blend
                min_votes = int(self._settings.blend_min_confirming_votes)
                bull_votes = int(votes.get("bull_votes", 0))
                bear_votes = int(votes.get("bear_votes", 0))

                if (
                    crossed_long
                    and bull_votes >= min_votes
                    and not rsi_block_long
                ):
                    sig = plan_directional_signal(
                        symbol=symbol,
                        target_side=+1,
                        entry_qty=entry_qty,
                        position_qty=pos_qty,
                        reason_open=self._format_entry_reason("long", votes, blend),
                        reason_close="blend_long_close",
                        score=score,
                    )
                elif (
                    crossed_short
                    and bear_votes >= min_votes
                    and not rsi_block_short
                ):
                    sig = plan_directional_signal(
                        symbol=symbol,
                        target_side=-1,
                        entry_qty=entry_qty,
                        position_qty=pos_qty,
                        reason_open=self._format_entry_reason("short", votes, blend),
                        reason_close="blend_short_close",
                        score=score,
                    )
                elif crossed_long or crossed_short:
                    self._log_entry_near_miss(
                        symbol=symbol,
                        blend=blend,
                        prev=prev,
                        crossed_long=crossed_long,
                        crossed_short=crossed_short,
                        bull_votes=bull_votes,
                        bear_votes=bear_votes,
                        min_votes=min_votes,
                        rsi_block_long=bool(rsi_block_long),
                        rsi_block_short=bool(rsi_block_short),
                    )

            if sig is None:
                continue
            state.last_action_ts = now
            signal_log_emit(
                logger,
                f"BLEND {'close' if sig.reduce_only else 'open'} -> {sig.side.value.upper()} "
                f"{symbol} qty={sig.qty:.10f} blend={blend:.3f}",
                reason=sig.reason,
            )
            signals.append(sig)

        signals = self._cap_entries(signals)
        self._maybe_log_scan(
            now=now,
            quoted=quoted,
            warming=warming,
            ready=ready,
            signal_count=len(signals),
            best_symbol=best_symbol,
            best_blend=best_blend,
            best_bull=best_bull,
            best_bear=best_bear,
        )
        return signals

    def _min_bars_required(self) -> int:
        s = self._settings
        return max(
            s.blend_ema_slow,
            s.blend_bb_period,
            s.blend_macd_slow + s.blend_macd_signal,
            s.blend_rsi_period + 1,
        )

    def _component_votes(
        self,
        state: _IndicatorState,
        close: float,
        feat: Features,
    ) -> dict[str, float | int | bool]:
        s = self._settings
        votes: dict[str, float | int | bool] = {}
        bull = bear = 0

        # --- EMA trend ---
        state.ema_fast = ema_step(state.ema_fast, close, s.blend_ema_fast)
        state.ema_slow = ema_step(state.ema_slow, close, s.blend_ema_slow)
        if state.ema_fast > state.ema_slow:
            votes["ema"] = 1.0
            bull += 1
        elif state.ema_fast < state.ema_slow:
            votes["ema"] = -1.0
            bear += 1
        else:
            votes["ema"] = 0.0

        # --- MACD ---
        macd_line, sig_line, _, state.macd_signal, state.ema_macd_fast, state.ema_macd_slow = (
            macd_step(
                ema_fast=state.ema_macd_fast,
                ema_slow=state.ema_macd_slow,
                signal=state.macd_signal,
                price=close,
                fast_period=s.blend_macd_fast,
                slow_period=s.blend_macd_slow,
                signal_period=s.blend_macd_signal,
            )
        )
        if macd_line > sig_line:
            votes["macd"] = 1.0
            bull += 1
        elif macd_line < sig_line:
            votes["macd"] = -1.0
            bear += 1
        else:
            votes["macd"] = 0.0

        # --- RSI ---
        rsi = rsi_from_closes(state.closes, s.blend_rsi_period)
        if rsi is not None:
            if s.blend_rsi_long_low <= rsi <= s.blend_rsi_long_high:
                votes["rsi"] = 1.0
                bull += 1
            elif s.blend_rsi_short_low <= rsi <= s.blend_rsi_short_high:
                votes["rsi"] = -1.0
                bear += 1
            else:
                votes["rsi"] = 0.0
            votes["rsi_block_long"] = rsi >= s.blend_rsi_overbought
            votes["rsi_block_short"] = rsi <= s.blend_rsi_oversold
            votes["rsi_val"] = rsi

        # --- Bollinger %B (mean reversion at extremes) ---
        bb = bollinger_bands(
            state.closes,
            period=s.blend_bb_period,
            std_mult=s.blend_bb_std,
        )
        if bb is not None:
            _, _, _, pct_b = bb
            if pct_b <= s.blend_bb_long_pct:
                votes["bb"] = 1.0
                bull += 1
            elif pct_b >= s.blend_bb_short_pct:
                votes["bb"] = -1.0
                bear += 1
            else:
                votes["bb"] = 0.0
            votes["pct_b"] = pct_b

        # --- Microstructure (imbalance + tape, same spirit as MM) ---
        imb = float(feat.imbalance_topn or 0.0)
        tape_num = (feat.ask_hit_ratio or 0.0) - (feat.bid_hit_ratio or 0.0)
        micro = (
            s.blend_micro_imbalance_scale * imb
            + s.blend_micro_tape_scale * tape_num
        )
        if micro >= s.blend_micro_threshold:
            votes["micro"] = 1.0
            bull += 1
        elif micro <= -s.blend_micro_threshold:
            votes["micro"] = -1.0
            bear += 1
        else:
            votes["micro"] = 0.0

        votes["bull_votes"] = bull
        votes["bear_votes"] = bear
        return votes

    def _blend_score(
        self, votes: dict[str, float | int | bool]
    ) -> tuple[float | None, float]:
        s = self._settings
        weights = {
            "ema": s.blend_weight_ema,
            "macd": s.blend_weight_macd,
            "rsi": s.blend_weight_rsi,
            "bb": s.blend_weight_bb,
            "micro": s.blend_weight_micro,
        }
        num = 0.0
        den = 0.0
        for key, w in weights.items():
            if w <= 0:
                continue
            v = votes.get(key)
            if v is None or not isinstance(v, (int, float)):
                continue
            num += float(v) * w
            den += w
        if den <= 0:
            return None, 0.0
        blend = max(-1.0, min(1.0, num / den))
        return blend, min(1.0, abs(blend))

    @staticmethod
    def _log_entry_near_miss(
        *,
        symbol: str,
        blend: float,
        prev: float,
        crossed_long: bool,
        crossed_short: bool,
        bull_votes: int,
        bear_votes: int,
        min_votes: int,
        rsi_block_long: bool,
        rsi_block_short: bool,
    ) -> None:
        if crossed_long:
            if rsi_block_long:
                logger.debug(
                    "BLEND %s long cross blocked: rsi overbought blend=%.3f prev=%.3f",
                    symbol,
                    blend,
                    prev,
                )
            elif bull_votes < min_votes:
                logger.debug(
                    "BLEND %s long cross blocked: bull_votes=%d need=%d blend=%.3f",
                    symbol,
                    bull_votes,
                    min_votes,
                    blend,
                )
        elif crossed_short:
            if rsi_block_short:
                logger.debug(
                    "BLEND %s short cross blocked: rsi oversold blend=%.3f prev=%.3f",
                    symbol,
                    blend,
                    prev,
                )
            elif bear_votes < min_votes:
                logger.debug(
                    "BLEND %s short cross blocked: bear_votes=%d need=%d blend=%.3f",
                    symbol,
                    bear_votes,
                    min_votes,
                    blend,
                )

    @staticmethod
    def _format_entry_reason(side: str, votes: dict[str, float | int | bool], blend: float) -> str:
        parts = [
            f"blend_{side}",
            f"score={blend:.3f}",
            f"ema={votes.get('ema', 0):+.0f}",
            f"macd={votes.get('macd', 0):+.0f}",
            f"rsi={votes.get('rsi', 0):+.0f}",
            f"bb={votes.get('bb', 0):+.0f}",
            f"micro={votes.get('micro', 0):+.0f}",
        ]
        rsi_val = votes.get("rsi_val")
        if isinstance(rsi_val, (int, float)):
            parts.append(f"rsi={rsi_val:.1f}")
        return " ".join(parts)

    def _cap_entries(self, signals: list[Signal]) -> list[Signal]:
        max_n = int(getattr(self._settings, "blend_max_entries_per_tick", 0) or 0)
        if max_n <= 0:
            return signals
        exits = [s for s in signals if s.reduce_only]
        entries = [s for s in signals if not s.reduce_only]
        if len(entries) <= max_n:
            return signals
        entries.sort(key=lambda s: -float(s.score))
        return exits + entries[:max_n]

    def _maybe_log_scan(
        self,
        *,
        now: float,
        quoted: int,
        warming: int,
        ready: int,
        signal_count: int,
        best_symbol: str,
        best_blend: float,
        best_bull: int,
        best_bear: int,
    ) -> None:
        interval = float(self._settings.blend_scan_log_interval_sec)
        if interval <= 0:
            return
        if self._last_scan_log_ts > 0 and now - self._last_scan_log_ts < interval:
            return
        self._last_scan_log_ts = now
        entry = float(self._settings.blend_entry_threshold)
        logger.info(
            "BLEND scan: universe=%d quoted=%d ready=%d warming=%d "
            "best=%s blend=%.3f bull=%d bear=%d entry=%.2f signals=%d",
            len(self._symbols),
            quoted,
            ready,
            warming,
            best_symbol or "-",
            best_blend,
            best_bull,
            best_bear,
            entry,
            signal_count,
        )

    def _push_sample(self, state: _IndicatorState, mid: float, now: float) -> bool:
        if self._bar_interval_sec <= 0:
            state.closes.append(mid)
            maxlen = self._min_bars_required() + 5
            while len(state.closes) > maxlen:
                state.closes.popleft()
            return True
        return self._append_bar_close_if_advanced(state, mid, now)

    def _append_bar_close_if_advanced(
        self, state: _IndicatorState, mid: float, now: float
    ) -> bool:
        interval = self._bar_interval_sec
        bucket = int(now // interval)
        if state.bar_bucket is None:
            state.bar_bucket = bucket
            state.last_close_in_bar = mid
            return False
        if bucket == state.bar_bucket:
            state.last_close_in_bar = mid
            return False
        close_px = state.last_close_in_bar
        state.closes.append(close_px)
        maxlen = self._min_bars_required() + 5
        while len(state.closes) > maxlen:
            state.closes.popleft()
        state.bar_bucket = bucket
        state.last_close_in_bar = mid
        return True

    def _state_for(self, symbol: str) -> _IndicatorState:
        state = self._state.get(symbol)
        if state is None:
            state = _IndicatorState(closes=deque())
            self._state[symbol] = state
        return state

    def _size_for(self, mid: float) -> float:
        provider = self._equity_provider
        if provider is None:
            return float(self._settings.blend_qty)
        try:
            equity = float(provider())
        except Exception:  # noqa: BLE001
            logger.warning("equity provider raised; using blend_qty default")
            return float(self._settings.blend_qty)
        if equity <= 0:
            return float(self._settings.blend_qty)
        universe_n = max(1, len(self._symbols))
        risk_pct = float(self._settings.blend_risk_per_trade_pct) / universe_n
        stop_pct = float(self._settings.default_stop_loss_pct)
        if risk_pct <= 0 or stop_pct <= 0 or mid <= 0:
            return float(self._settings.blend_qty)
        notional = (equity * risk_pct) / stop_pct
        return max(0.0, notional / mid)
