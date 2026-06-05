"""ADX-gated multi-indicator blend on closed bars (15m default).

Five voters: EMA trend, MACD momentum, RSI, Bollinger %B, microstructure.
Momentum (EMA/MACD) and mean-reversion (RSI/BB) are never averaged without an
ADX regime gate. Indicators compute on bar close only; micro uses live book.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

from common.config import Settings
from common.logging import signal_log_emit
from common.types import Signal

from ..market_data.feature_store import Features
from .indicators import (
    AdxWilderState,
    RsiWilderState,
    adx_wilder_step,
    bollinger_bands,
    ema_seed_from_closes,
    macd_step,
    rsi_wilder_seed_from_closes,
    rsi_wilder_step,
)
from .position_sync import plan_directional_signal, side_from_qty
from .signal_scaling import conviction_above_entry, cubic_scaled_qty
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

EquityProvider = Callable[[], float]
Mm2SymbolsProvider = Callable[[], frozenset[str]]


class BlendRegime(str, Enum):
    WARMUP = "WARMUP"
    RANGING = "RANGING"
    TRENDING = "TRENDING"


TRENDING_WEIGHTS: dict[str, float] = {
    "ema": 0.35,
    "macd": 0.30,
    "micro": 0.35,
}
RANGING_WEIGHTS: dict[str, float] = {
    "rsi": 0.35,
    "bb": 0.40,
    "micro": 0.25,
}


@dataclass(slots=True)
class _IndicatorState:
    closes: deque[float] = field(default_factory=deque)
    bar_high: float = 0.0
    bar_low: float = 0.0
    completed_high: float = 0.0
    completed_low: float = 0.0
    last_close_in_bar: float = 0.0
    bar_bucket: int | None = None
    bar_count: int = 0
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_macd_fast: float | None = None
    ema_macd_slow: float | None = None
    macd_signal: float | None = None
    prev_histogram: float | None = None
    rsi: RsiWilderState = field(default_factory=RsiWilderState)
    adx: AdxWilderState = field(default_factory=AdxWilderState)
    regime: BlendRegime = BlendRegime.WARMUP
    adx_value: float | None = None
    prev_blend: float | None = None
    last_action_ts: float = 0.0
    entry_regime: BlendRegime | None = None
    warmup_logged: bool = False


class BlendedSignalsStrategy(StrategyBase):
    name = "blended_signals"
    display_label = "Blended Signals"
    description = "ADX-gated EMA/MACD/RSI/BB blend on closed bars + micro confirm"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)
        self._bar_interval_sec = float(settings.blend_bar_interval_sec or 0.0)
        if self._bar_interval_sec <= 0:
            raise ValueError("BLEND_BAR_INTERVAL_SEC must be > 0 (e.g. 900 for 15m bars)")
        self._state: dict[str, _IndicatorState] = {}
        self._equity_provider: EquityProvider | None = None
        self._mm2_symbols_provider: Mm2SymbolsProvider | None = None
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
            if len(configured) == 1 and configured[0] == "AUTO":
                logger.warning(
                    "BLEND_SYMBOLS=AUTO disabled for blend refactor; set explicit symbols"
                )
                return []
            return sorted(set(configured))
        legacy = (settings.blend_symbol or "").strip().upper()
        if legacy:
            return [legacy]
        return ["BTCUSDT", "ETHUSDT"]

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        self._equity_provider = provider

    def attach_mm2_active_symbols_provider(self, provider: Mm2SymbolsProvider) -> None:
        self._mm2_symbols_provider = provider

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._symbols = self._resolve_universe(settings)
        self._bar_interval_sec = float(settings.blend_bar_interval_sec or 0.0)
        if self._bar_interval_sec <= 0:
            raise ValueError("BLEND_BAR_INTERVAL_SEC must be > 0")
        self._validate_windows(settings)

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        logger.debug("blend on_fill %s %s qty=%.8f", symbol, side, qty)

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        cooldown = float(self._settings.blend_cooldown_sec)
        signals: list[Signal] = []
        quoted = warming = ready = 0

        for symbol in self._symbols:
            feat = features.get(symbol)
            if feat is None or feat.mid is None or feat.mid <= 0:
                continue
            mid = float(feat.mid)
            if mid < float(self._settings.blend_min_mid_price):
                continue
            quoted += 1
            state = self._state_for(symbol)

            if not self._feed_bar(state, mid, now):
                continue

            min_bars = self._min_bars_required()
            if len(state.closes) < min_bars:
                warming += 1
                if not state.warmup_logged:
                    logger.info(
                        "[blend] %s warmup: %d/%d bars collected, no signals until full",
                        symbol,
                        len(state.closes),
                        min_bars,
                    )
                    state.warmup_logged = True
                continue
            ready += 1
            state.warmup_logged = False

            if now - state.last_action_ts < cooldown:
                continue

            bar_signals = self._on_bar_close(symbol, state, feat, now)
            signals.extend(bar_signals)

        signals = self._cap_entries(signals)
        self._maybe_log_scan(now=now, quoted=quoted, warming=warming, ready=ready, n_sig=len(signals))
        return signals

    def _on_bar_close(
        self,
        symbol: str,
        state: _IndicatorState,
        feat: Features,
        now: float,
    ) -> list[Signal]:
        s = self._settings
        close = state.closes[-1]
        votes = self._component_votes(state, close, feat, symbol)
        regime = state.regime
        blend, score, bull_votes, bear_votes = self._blend_score(votes, regime)
        if blend is None:
            self._log_bar(symbol, state, votes, 0.0, action="WARMUP")
            return []

        pos_qty = self._position_qty(symbol)
        pos_side = side_from_qty(pos_qty)
        entry_thresh = float(s.blend_entry_threshold)
        exit_thresh = float(s.blend_exit_threshold)
        min_votes = int(s.blend_min_confirming_votes)

        sig: Signal | None = None
        prev = state.prev_blend
        state.prev_blend = blend

        p_max = self._size_for(close)
        if p_max <= 0:
            return []

        if self._regime_flip_exit(state, pos_side):
            sig = plan_directional_signal(
                symbol=symbol,
                target_side=0,
                entry_qty=p_max,
                position_qty=pos_qty,
                reason_open="blend_regime_entry",
                reason_close=f"blend_regime_flip adx={state.adx_value:.1f}",
                score=score,
            )
            state.entry_regime = None
        elif pos_side == 1 and blend < exit_thresh:
            sig = plan_directional_signal(
                symbol=symbol,
                target_side=0,
                entry_qty=p_max,
                position_qty=pos_qty,
                reason_open="blend_long",
                reason_close=f"blend_exit_long blend={blend:.3f}",
                score=score,
            )
            state.entry_regime = None
        elif pos_side == -1 and blend > -exit_thresh:
            sig = plan_directional_signal(
                symbol=symbol,
                target_side=0,
                entry_qty=p_max,
                position_qty=pos_qty,
                reason_open="blend_short",
                reason_close=f"blend_exit_short blend={blend:.3f}",
                score=score,
            )
            state.entry_regime = None
        elif pos_side == 0 and prev is not None and regime != BlendRegime.WARMUP:
            rsi_block_long = bool(votes.get("rsi_block_long"))
            rsi_block_short = bool(votes.get("rsi_block_short"))
            crossed_long = prev < entry_thresh <= blend
            crossed_short = prev > -entry_thresh >= blend

            if crossed_long and bull_votes >= min_votes and not rsi_block_long:
                signal = conviction_above_entry(
                    blend, entry=entry_thresh, full=1.0,
                )
                entry_qty = cubic_scaled_qty(p_max, signal)
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=+1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=self._format_entry_reason("long", votes, blend, regime),
                    reason_close="blend_long_close",
                    score=score,
                )
                state.entry_regime = regime
            elif crossed_short and bear_votes >= min_votes and not rsi_block_short:
                signal = conviction_above_entry(
                    blend, entry=entry_thresh, full=1.0,
                )
                entry_qty = cubic_scaled_qty(p_max, signal)
                sig = plan_directional_signal(
                    symbol=symbol,
                    target_side=-1,
                    entry_qty=entry_qty,
                    position_qty=pos_qty,
                    reason_open=self._format_entry_reason("short", votes, blend, regime),
                    reason_close="blend_short_close",
                    score=score,
                )
                state.entry_regime = regime

        action = "FLAT"
        if sig is not None:
            if sig.reduce_only:
                action = "FLAT"
            elif sig.side.value == "BUY":
                action = "LONG"
            else:
                action = "SHORT"
        elif votes.get("suppressed"):
            action = "SUPPRESS"
        self._log_bar(symbol, state, votes, blend, action=action)

        if sig is None:
            return []

        state.last_action_ts = now
        signal_log_emit(
            logger,
            f"BLEND {'close' if sig.reduce_only else 'open'} -> {sig.side.value.upper()} "
            f"{symbol} qty={sig.qty:.10f} blend={blend:.3f} regime={regime.value}",
            reason=sig.reason,
        )
        return [sig]

    def _regime_flip_exit(self, state: _IndicatorState, pos_side: int) -> bool:
        if pos_side == 0 or not bool(self._settings.blend_regime_flip_exit):
            return False
        if state.entry_regime != BlendRegime.TRENDING:
            return False
        adx = state.adx_value
        if adx is None:
            return False
        threshold = float(self._settings.blend_adx_trend_threshold)
        buffer = float(self._settings.blend_regime_flip_adx_buffer)
        return adx < threshold - buffer

    def _min_bars_required(self) -> int:
        s = self._settings
        return max(
            s.blend_ema_slow,
            s.blend_bb_period,
            s.blend_macd_slow + s.blend_macd_signal,
            s.blend_rsi_period + 1,
            s.blend_adx_period + 1,
        )

    def _update_regime(self, state: _IndicatorState, high: float, low: float, close: float) -> None:
        s = self._settings
        adx = adx_wilder_step(
            state.adx,
            high=high,
            low=low,
            close=close,
            period=int(s.blend_adx_period),
        )
        state.adx_value = adx
        if adx is None:
            state.regime = BlendRegime.WARMUP
            return
        if adx >= float(s.blend_adx_trend_threshold):
            state.regime = BlendRegime.TRENDING
        else:
            state.regime = BlendRegime.RANGING

    def _component_votes(
        self,
        state: _IndicatorState,
        close: float,
        feat: Features,
        symbol: str,
    ) -> dict[str, float | int | bool]:
        s = self._settings
        regime = state.regime
        votes: dict[str, float | int | bool] = {
            "ema": 0.0,
            "macd": 0.0,
            "rsi": 0.0,
            "bb": 0.0,
            "micro": 0.0,
            "rsi_block_long": False,
            "rsi_block_short": False,
            "suppressed": False,
        }

        self._update_regime(state, state.completed_high, state.completed_low, close)

        # --- EMA trend (TRENDING only) ---
        state.ema_fast = ema_seed_from_closes(state.closes, state.ema_fast, s.blend_ema_fast)
        state.ema_slow = ema_seed_from_closes(state.closes, state.ema_slow, s.blend_ema_slow)
        ema_vote = 0.0
        if (
            regime == BlendRegime.TRENDING
            and state.ema_fast is not None
            and state.ema_slow is not None
            and state.ema_slow > 0
        ):
            gap_bps = (state.ema_fast - state.ema_slow) / state.ema_slow * 10_000.0
            if gap_bps > float(s.blend_ema_min_gap_bps):
                ema_vote = 1.0
            elif gap_bps < -float(s.blend_ema_min_gap_bps):
                ema_vote = -1.0
        votes["ema"] = ema_vote

        # --- MACD histogram direction (TRENDING only) ---
        macd_vote = 0.0
        if regime == BlendRegime.TRENDING:
            _, _, hist, new_sig, state.ema_macd_fast, state.ema_macd_slow = macd_step(
                ema_fast=state.ema_macd_fast,
                ema_slow=state.ema_macd_slow,
                signal=state.macd_signal,
                price=close,
                fast_period=s.blend_macd_fast,
                slow_period=s.blend_macd_slow,
                signal_period=s.blend_macd_signal,
            )
            state.macd_signal = new_sig
            prev_h = state.prev_histogram
            state.prev_histogram = hist
            if prev_h is not None:
                if hist > 0 and hist > prev_h:
                    macd_vote = 1.0
                elif hist < 0 and hist < prev_h:
                    macd_vote = -1.0
        votes["macd"] = macd_vote

        # --- RSI Wilder ---
        rsi_val: float | None = None
        if not state.rsi.seeded and len(state.closes) >= s.blend_rsi_period + 1:
            rsi_val = rsi_wilder_seed_from_closes(state.rsi, state.closes, s.blend_rsi_period)
        else:
            rsi_val = rsi_wilder_step(state.rsi, close, s.blend_rsi_period)

        trend_dir = 0
        if ema_vote > 0 or macd_vote > 0:
            trend_dir = 1
        elif ema_vote < 0 or macd_vote < 0:
            trend_dir = -1

        if rsi_val is not None:
            votes["rsi_val"] = rsi_val
            if regime == BlendRegime.RANGING:
                if rsi_val < float(s.blend_rsi_oversold):
                    votes["rsi"] = 1.0
                elif rsi_val > float(s.blend_rsi_overbought):
                    votes["rsi"] = -1.0
            elif regime == BlendRegime.TRENDING:
                strong = (
                    state.adx_value is not None
                    and state.adx_value >= float(s.blend_adx_strong_threshold)
                )
                if not strong:
                    if rsi_val >= float(s.blend_rsi_extreme_overbought) and trend_dir > 0:
                        votes["rsi_block_long"] = True
                        votes["suppressed"] = True
                    if rsi_val <= float(s.blend_rsi_extreme_oversold) and trend_dir < 0:
                        votes["rsi_block_short"] = True
                        votes["suppressed"] = True

        if votes["rsi_block_long"] or votes["rsi_block_short"]:
            votes["ema"] = 0.0
            votes["macd"] = 0.0
            ema_vote = 0.0
            macd_vote = 0.0

        # --- Bollinger %B (RANGING only) ---
        bb_vote = 0.0
        if regime == BlendRegime.RANGING:
            bb = bollinger_bands(
                state.closes,
                period=s.blend_bb_period,
                std_mult=s.blend_bb_std,
            )
            if bb is not None:
                _, _, _, pct_b = bb
                votes["pct_b"] = pct_b
                if pct_b < float(s.blend_bb_lower_threshold):
                    bb_vote = 1.0
                elif pct_b > float(s.blend_bb_upper_threshold):
                    bb_vote = -1.0
        votes["bb"] = bb_vote

        # --- Microstructure (both regimes; skip when MM2 active on symbol) ---
        micro_vote = 0.0
        mm2_active = self._mm2_active_symbols()
        if symbol not in mm2_active:
            imb = float(feat.imbalance_topn or 0.0)
            tape = float(feat.ask_hit_ratio or 0.0) - float(feat.bid_hit_ratio or 0.0)
            micro = 0.6 * imb + 0.4 * tape
            thr = float(s.blend_micro_threshold)
            if micro > thr:
                micro_vote = 1.0
            elif micro < -thr:
                micro_vote = -1.0
        votes["micro"] = micro_vote

        return votes

    def _blend_score(
        self,
        votes: dict[str, float | int | bool],
        regime: BlendRegime,
    ) -> tuple[float | None, float, int, int]:
        if regime == BlendRegime.WARMUP:
            return None, 0.0, 0, 0
        weights = TRENDING_WEIGHTS if regime == BlendRegime.TRENDING else RANGING_WEIGHTS
        num = 0.0
        den = 0.0
        bull = bear = 0
        for key, w in weights.items():
            if w <= 0:
                continue
            v = votes.get(key, 0.0)
            if not isinstance(v, int | float):
                continue
            fv = float(v)
            num += fv * w
            den += w
            if fv > 0:
                bull += 1
            elif fv < 0:
                bear += 1
        if den <= 0:
            return None, 0.0, 0, 0
        blend = max(-1.0, min(1.0, num / den))
        return blend, min(1.0, abs(blend)), bull, bear

    def _log_bar(
        self,
        symbol: str,
        state: _IndicatorState,
        votes: dict[str, float | int | bool],
        blend: float,
        *,
        action: str,
    ) -> None:
        adx = state.adx_value
        adx_s = f"{adx:.1f}" if adx is not None else "-"
        close = state.closes[-1] if state.closes else 0.0
        logger.info(
            "[blend] %s bar=%d regime=%s ADX=%s close=%.4f "
            "ema_vote=%+.0f macd_vote=%+.0f rsi_vote=%+.0f bb_vote=%+.0f micro_vote=%+.0f "
            "blend_score=%.3f action=%s",
            symbol,
            state.bar_count,
            state.regime.value,
            adx_s,
            close,
            votes.get("ema", 0),
            votes.get("macd", 0),
            votes.get("rsi", 0),
            votes.get("bb", 0),
            votes.get("micro", 0),
            blend,
            action,
        )

    def _mm2_active_symbols(self) -> frozenset[str]:
        provider = self._mm2_symbols_provider
        if provider is None:
            return frozenset()
        try:
            return provider()
        except Exception:  # noqa: BLE001
            return frozenset()

    @staticmethod
    def _format_entry_reason(
        side: str,
        votes: dict[str, float | int | bool],
        blend: float,
        regime: BlendRegime,
    ) -> str:
        parts = [
            f"blend_{side}",
            f"regime={regime.value}",
            f"score={blend:.3f}",
            f"ema={votes.get('ema', 0):+.0f}",
            f"macd={votes.get('macd', 0):+.0f}",
            f"rsi={votes.get('rsi', 0):+.0f}",
            f"bb={votes.get('bb', 0):+.0f}",
            f"micro={votes.get('micro', 0):+.0f}",
        ]
        rsi_val = votes.get("rsi_val")
        if isinstance(rsi_val, int | float):
            parts.append(f"rsi_val={rsi_val:.1f}")
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
        n_sig: int,
    ) -> None:
        interval = float(self._settings.blend_scan_log_interval_sec)
        if interval <= 0:
            return
        if self._last_scan_log_ts > 0 and now - self._last_scan_log_ts < interval:
            return
        self._last_scan_log_ts = now
        logger.info(
            "BLEND scan: universe=%d quoted=%d ready=%d warming=%d signals=%d",
            len(self._symbols),
            quoted,
            ready,
            warming,
            n_sig,
        )

    def _feed_bar(self, state: _IndicatorState, mid: float, now: float) -> bool:
        """Update OHLC bar from ticks; return True when a bar closed."""
        interval = self._bar_interval_sec
        bucket = int(now // interval)
        if state.bar_bucket is None:
            state.bar_bucket = bucket
            state.last_close_in_bar = mid
            state.bar_high = mid
            state.bar_low = mid
            return False
        if bucket == state.bar_bucket:
            state.last_close_in_bar = mid
            state.bar_high = max(state.bar_high, mid)
            state.bar_low = min(state.bar_low, mid)
            return False

        close_px = state.last_close_in_bar
        state.completed_high = state.bar_high
        state.completed_low = state.bar_low
        state.closes.append(close_px)
        state.bar_count += 1
        maxlen = self._min_bars_required() + 5
        while len(state.closes) > maxlen:
            state.closes.popleft()

        state.bar_bucket = bucket
        state.last_close_in_bar = mid
        state.bar_high = mid
        state.bar_low = mid
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
