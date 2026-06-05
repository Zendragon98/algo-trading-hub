"""Cross-coin USDT/USDC basis trading.

The Binance Futures Testnet doesn't list a tradable USDT/USDC perp, but
every BTC, ETH, SOL ... pair quoted in *both* stables gives us an
*implied* USDT/USDC rate:

    BTC = BTCUSDT * USDT = BTCUSDC * USDC
    =>  USDT / USDC  =  BTCUSDC / BTCUSDT
    =>  log(USDT/USDC)  =  log(BTCUSDC) - log(BTCUSDT)

We call this the per-coin "basis". The reference is configurable
(``pair_reference_mode``): BTC-anchored (default), volume-weighted mean,
or independent per-coin basis. Deviation samples are bar-aggregated when
``pair_bar_sec > 0`` so z-scores are not tick-noisy.

    basis_i      = log(coin_USDC) - log(coin_USDT)
    reference    = BTC basis (btc_anchor) | weighted mean | 0 (independent)
    deviation_i  = basis_i - reference
    z_i          = (deviation_i - rolling_mean) / rolling_std

    z_i >= +entry_z  ->  SHORT coin_USDC, LONG coin_USDT (USDC leg rich)
    z_i <= -entry_z  ->  LONG  coin_USDC, SHORT coin_USDT (USDT leg rich)
    |z_i| <= exit_z  ->  unwind

The weights ``w_i`` are the per-coin 24h notional volume on the venue
(a market-cap proxy that's locally observable). The engine refreshes
the cache every ``PAIR_VOLUME_REFRESH_SEC`` and the strategy picks it
up via ``attach_weight_provider``. Falls back to equal weights while
the cache is empty so cold-start behaviour is identical to the
unweighted strategy.

Sizing is hybrid: a stop-loss budget at ``risk_per_trade_pct`` sets
``P_floor``, then cubic conviction scaling grows toward ``P_ceil`` with
``s = 0`` at ``entry_z`` and ``s = 1`` at ``entry_z × pair_size_scale_cap``.
Bigger deviations => stronger signal => bigger size. Both legs of a coin pair share
``group_id`` so the engine submits them atomically; no "naked leg" can
leak through a venue filter rejection.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from common.config import Settings
from common.enums import Side
from common.logging import signal_log_emit
from common.types import Signal

from ..market_data.feature_store import Features
from ..position.venue_pnl import apply_attributed_fill_vwap
from .signal_scaling import conviction_above_entry, cubic_scaled_qty
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)

_BTC_PAIR_KEY = "BTCUSDT|BTCUSDC"
_ETH_PAIR_KEY = "ETHUSDT|ETHUSDC"
_REFERENCE_LOG_INTERVAL_SEC = 10.0


# Snapshot returned by the engine. We only need `.equity` so any object
# with that attribute satisfies the contract — keeps the strategy
# decoupled from the concrete `PortfolioSnapshot` type.
EquityProvider = Callable[[], float]

# Returns the latest 24h notional volume per symbol (e.g. ``{"BTCUSDT":
# 1.2e9, "BTCUSDC": 8.0e7, ...}``). The strategy multiplies the USDT +
# USDC legs to get a per-coin liquidity weight; missing keys collapse
# to equal weights for that coin so the cache being stale never blocks
# the consensus reference.
WeightProvider = Callable[[], dict[str, float]]


@dataclass(slots=True)
class PairConfig:
    """One coin traded in both USDT and USDC."""

    usdt_symbol: str
    usdc_symbol: str
    z_window_sec: int = 600
    entry_z: float = 2.0
    exit_z: float = 0.5
    # Hard stop in basis-spread space. If z keeps diverging past the
    # entry threshold by this much (against the open direction), give up
    # on mean-reversion and unwind. This is the *correct* SL surface for
    # a basis trade — per-leg fixed-% stops fire on correlated ticks
    # where the pair is actually fine. See
    # `engine.risk.stop_loss.StopLossMonitor` for how the per-leg
    # bracket is bypassed for symbols owned by this strategy.
    stop_z: float = 4.0


class _DeviationStats:
    """Rolling mean + std of the per-coin deviation from consensus."""

    __slots__ = (
        "samples",
        "window_sec",
        "_sum",
        "_sumsq",
        "open_side",
        "open_qty",
        "open_ts",
        "open_usdt_mid",
        "open_usdc_mid",
        "last_action_ts",
        "pending_side",
        "pending_qty",
        "pending_usdt",
        "pending_usdc",
        "pending_fills_remaining",
        "pending_since_ts",
        "pending_is_close",
        "pending_filled_symbol",
        "pending_entry_usdt_mid",
        "pending_entry_usdc_mid",
        "pending_entry_usdt_qty",
        "pending_entry_usdc_qty",
        "min_samples",
    )

    def __init__(self, window_sec: int, min_samples: int = 30) -> None:
        self.samples: deque[tuple[float, float]] = deque()
        self.window_sec = int(window_sec)
        self._sum: float = 0.0
        self._sumsq: float = 0.0
        # +1 = short USDC + long USDT (we faded a rich USDC leg)
        # -1 = long  USDC + short USDT (we faded a rich USDT leg)
        #  0 = flat
        self.open_side: int = 0
        # Base qty actually opened. Stored so the unwind sends the same
        # size and not a freshly-resized batch (which could leave dust).
        self.open_qty: float = 0.0
        self.open_ts: float = 0.0
        self.open_usdt_mid: float = 0.0
        self.open_usdc_mid: float = 0.0
        # Prevent churn: last time we emitted any order intent for this pair.
        self.last_action_ts: float = 0.0

        # Pending fill state. We do not consider ourselves open/closed until
        # both legs report fills (strategies receive fills via Engine.on_fill).
        self.pending_side: int = 0
        self.pending_qty: float = 0.0
        self.pending_usdt: str = ""
        self.pending_usdc: str = ""
        self.pending_fills_remaining: int = 0
        self.pending_since_ts: float = 0.0
        self.pending_is_close: bool = False
        self.pending_filled_symbol: str = ""
        self.pending_entry_usdt_mid: float = 0.0
        self.pending_entry_usdc_mid: float = 0.0
        self.pending_entry_usdt_qty: float = 0.0
        self.pending_entry_usdc_qty: float = 0.0
        self.min_samples = max(1, int(min_samples))

    def push(self, ts: float, value: float) -> None:
        self.samples.append((ts, value))
        self._sum += value
        self._sumsq += value * value
        # Evict samples older than the rolling time window.
        cutoff = ts - float(self.window_sec)
        while self.samples and self.samples[0][0] < cutoff:
            _, evicted = self.samples.popleft()
            self._sum -= evicted
            self._sumsq -= evicted * evicted

    def zscore(self, value: float) -> float | None:
        n = len(self.samples)
        if n < self.min_samples:
            return None
        mean = self._sum / n
        var = max(self._sumsq / n - mean * mean, 0.0)
        std = var ** 0.5
        if std <= 0:
            return None
        return (value - mean) / std


class PairsTradingStrategy(StrategyBase):
    """Cross-coin USDT/USDC basis-deviation strategy."""

    name = "pairs_trading_usdt_usdc"
    display_label = "Pairs Trading"
    description = "USDT/USDC cross-coin basis"

    # Fallback per-leg base qty when no equity provider is wired (used by
    # unit tests that don't construct a Portfolio). Production always
    # injects an equity provider via `attach_equity_provider`.
    _FALLBACK_QTY: float = 0.001

    def __init__(
        self,
        settings: Settings,
        equity_provider: EquityProvider | None = None,
    ) -> None:
        self._settings = settings
        self._equity_provider = equity_provider
        self._weight_provider: WeightProvider | None = None
        self._pairs: list[PairConfig] = [
            PairConfig(
                usdt_symbol=usdt,
                usdc_symbol=usdc,
                z_window_sec=settings.pair_z_window_sec,
                entry_z=settings.pair_entry_z,
                exit_z=settings.pair_exit_z,
                stop_z=settings.pair_stop_z,
            )
            for usdt, usdc in settings.pair_legs()
        ]
        min_samples = int(settings.pair_min_z_samples)
        window_sec = self._effective_z_window_sec(settings)
        self._stats: dict[str, _DeviationStats] = {
            self._key(p): _DeviationStats(window_sec=window_sec, min_samples=min_samples)
            for p in self._pairs
        }
        # Reference series so we can log how far the cross-coin consensus
        # has drifted; useful when calibrating thresholds offline.
        self._reference_history: deque[float] = deque(maxlen=600)
        self._bar_bucket: int | None = None
        self._last_reference_log_ts: float = 0.0
        self._bar_interval_sec = max(0, int(settings.pair_bar_sec or 0))

        self._load_calibration(settings)

        if not self._pairs:
            logger.warning(
                "PairsTradingStrategy enabled but no USDT/USDC pairs found in SYMBOLS"
            )
        elif (
            len(self._pairs) < 2
            and (settings.pair_reference_mode or "").strip().lower() == "weighted"
        ):
            logger.warning(
                "Pairs trading needs >=2 matched USDT/USDC bases for a weighted "
                "consensus (got %d). Set SYMBOLS=AUTO or add more pairs.",
                len(self._pairs),
            )

    @staticmethod
    def _effective_z_window_sec(settings: Settings) -> int:
        bar_sec = max(0, int(settings.pair_bar_sec or 0))
        window = int(settings.pair_z_window_sec)
        if bar_sec > 0:
            return window * bar_sec
        return window

    def refresh_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._pairs = [
            PairConfig(
                usdt_symbol=usdt,
                usdc_symbol=usdc,
                z_window_sec=settings.pair_z_window_sec,
                entry_z=settings.pair_entry_z,
                exit_z=settings.pair_exit_z,
                stop_z=settings.pair_stop_z,
            )
            for usdt, usdc in settings.pair_legs()
        ]
        window_sec = self._effective_z_window_sec(settings)
        self._bar_interval_sec = max(0, int(settings.pair_bar_sec or 0))
        new_stats: dict[str, _DeviationStats] = {}
        for p in self._pairs:
            key = self._key(p)
            prev = self._stats.get(key)
            if prev is not None and prev.window_sec == window_sec:
                new_stats[key] = prev
            else:
                new_stats[key] = _DeviationStats(
                    window_sec=window_sec,
                    min_samples=int(settings.pair_min_z_samples),
                )
        self._stats = new_stats
        self._load_calibration(settings)

    def _load_calibration(self, settings: Settings) -> None:
        """Apply per-base entry/exit/stop z from pair JSON and symbol_calibration."""
        path_str = (settings.pair_calibration_path or "").strip()
        if not path_str:
            self._load_symbol_calibration_pairs(settings)
            return
        path = Path(path_str)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent.parent / path_str
        files: list[Path]
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(path.glob("pair_*.json"))
        else:
            logger.warning("pair_calibration_path not found: %s", path)
            self._load_symbol_calibration_pairs(settings)
            return

        cal: dict[str, tuple[float, float, float | None]] = {}
        for fpath in files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                logger.exception("failed to read pair calibration %s", fpath)
                continue
            base = str(data.get("base", "")).strip().upper()
            if not base:
                continue
            try:
                stop_raw = data.get("suggested_stop_z")
                stop_z = float(stop_raw) if stop_raw is not None else None
                cal[base] = (
                    float(data["suggested_entry_z"]),
                    float(data["suggested_exit_z"]),
                    stop_z,
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("invalid pair calibration payload in %s", fpath)
                continue

        if cal:
            self._apply_pair_calibration_map(cal)
        self._load_symbol_calibration_pairs(settings)

    def _load_symbol_calibration_pairs(self, settings: Settings) -> None:
        from ..market_data.symbol_calibration import load_symbol_calibration
        from .mm_calibrated import calibration_path

        path = calibration_path(settings)
        if not path:
            return
        unified = load_symbol_calibration(path)
        if not unified:
            return
        cal: dict[str, tuple[float, float, float | None]] = {}
        for pair in self._pairs:
            sym_cal = unified.get(pair.usdt_symbol.upper())
            if sym_cal is None:
                continue
            if sym_cal.pair_entry_z is None and sym_cal.pair_exit_z is None:
                continue
            base = pair.usdt_symbol.removesuffix("USDT").upper()
            cal[base] = (
                sym_cal.pair_entry_z if sym_cal.pair_entry_z is not None else pair.entry_z,
                sym_cal.pair_exit_z if sym_cal.pair_exit_z is not None else pair.exit_z,
                sym_cal.pair_stop_z,
            )
        if cal:
            self._apply_pair_calibration_map(cal)

    def _apply_pair_calibration_map(
        self, cal: dict[str, tuple[float, float, float | None]],
    ) -> None:
        for pair in self._pairs:
            base = pair.usdt_symbol.removesuffix("USDT").upper()
            thresholds = cal.get(base)
            if thresholds is None:
                continue
            pair.entry_z, pair.exit_z = thresholds[0], thresholds[1]
            if thresholds[2] is not None:
                pair.stop_z = thresholds[2]
            logger.info(
                "pairs calibration %s: entry_z=%.2f exit_z=%.2f stop_z=%.2f",
                base, pair.entry_z, pair.exit_z, pair.stop_z,
            )

    def attach_equity_provider(self, provider: EquityProvider) -> None:
        """Wire the equity callback after engine construction.

        main.py calls this once the Engine has built its Portfolio, so
        the strategy can size positions from live equity without circular
        imports during construction.
        """
        self._equity_provider = provider

    def attach_weight_provider(self, provider: WeightProvider) -> None:
        """Wire the per-symbol 24h volume reader.

        The strategy combines the USDT + USDC legs of each coin into a
        liquidity weight used by the consensus reference. ``provider``
        is called every tick so the engine can refresh the cache
        independently of strategy logic.
        """
        self._weight_provider = provider

    def symbols(self) -> list[str]:
        out: list[str] = []
        for pair in self._pairs:
            out.extend([pair.usdt_symbol, pair.usdc_symbol])
        return out

    def manages_own_risk(self) -> bool:
        # Pair risk is basis divergence (handled below via stop_z), not
        # any single leg's absolute price move. Skip the per-leg bracket.
        return True

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        now = time.time()
        # 1. Compute per-pair basis. Skip pairs whose mids aren't ready
        #    yet so a cold-start coin doesn't poison the consensus mean.
        bases: dict[str, float] = {}
        mids_by_pair: dict[str, tuple[float, float]] = {}
        for pair in self._pairs:
            usdt = features.get(pair.usdt_symbol)
            usdc = features.get(pair.usdc_symbol)
            if usdt is None or usdc is None:
                continue
            if usdt.mid is None or usdc.mid is None:
                continue
            if usdt.mid <= 0 or usdc.mid <= 0:
                continue
            min_mid = float(self._settings.pair_min_mid_price)
            if min_mid > 0 and (usdt.mid < min_mid or usdc.mid < min_mid):
                continue
            bases[self._key(pair)] = _basis(usdc.mid, usdt.mid)
            mids_by_pair[self._key(pair)] = (usdt.mid, usdc.mid)

        min_bases = self._min_bases_required()
        if len(bases) < min_bases:
            return []

        # 2. Reference basis (mode-dependent; see ``pair_reference_mode``).
        weights_by_symbol = self._fetch_weights()
        reference = self._compute_reference(bases, weights_by_symbol)
        self._reference_history.append(reference)
        self._maybe_log_reference(now, reference, bases, weights_by_symbol)

        push_sample = self._advance_bar(now)
        ref_mode = (self._settings.pair_reference_mode or "btc_anchor").strip().lower()
        use_reference = ref_mode != "independent"

        # 3. Per-pair deviation z-score and signal generation.
        signals: list[Signal] = []
        for pair in self._pairs:
            key = self._key(pair)
            basis = bases.get(key)
            if basis is None:
                continue
            deviation = basis if not use_reference else basis - reference
            stats = self._stats[key]
            if push_sample:
                stats.push(now, deviation)
            z = stats.zscore(deviation)
            if z is None:
                if push_sample:
                    logger.debug(
                        "[pairs] WARMUP %s samples=%d/%d reference_mode=%s",
                        pair.usdt_symbol.removesuffix("USDT"),
                        len(stats.samples),
                        stats.min_samples,
                        ref_mode,
                    )
                continue

            usdt_mid, usdc_mid = mids_by_pair[key]
            signals.extend(
                self._check_partial_pending(pair, stats, now),
            )
            if stats.open_side != 0 and stats.pending_fills_remaining == 0:
                signals.extend(
                    self._check_leg_loss_cap(pair, stats, usdt_mid, usdc_mid, z),
                )
            # Flat entries only on bar close when bar aggregation is enabled.
            should_evaluate = (
                stats.open_side != 0
                or push_sample
                or self._bar_interval_sec <= 0
            )
            if should_evaluate:
                signals.extend(
                    self._evaluate(pair, stats, z, basis, reference, usdt_mid, usdc_mid)
                )
        return self._cap_new_entries(signals)

    def _min_bases_required(self) -> int:
        mode = (self._settings.pair_reference_mode or "btc_anchor").strip().lower()
        if mode in ("independent", "btc_anchor"):
            return 1
        return 2

    def _advance_bar(self, now: float) -> bool:
        """Return True when a new bar closed and deviation samples should be pushed."""
        interval = self._bar_interval_sec
        if interval <= 0:
            return True
        bucket = int(now // interval)
        if self._bar_bucket is None:
            self._bar_bucket = bucket
            return False
        if bucket == self._bar_bucket:
            return False
        self._bar_bucket = bucket
        return True

    def _cap_new_entries(self, signals: list[Signal]) -> list[Signal]:
        """Limit simultaneous new pair opens (by group_id) so flatten stays bounded."""
        max_n = int(getattr(self._settings, "pair_max_new_entries_per_tick", 0) or 0)
        if max_n <= 0:
            return signals
        other = [s for s in signals if "pairs_open" not in (s.reason or "")]
        opens = [s for s in signals if "pairs_open" in (s.reason or "")]
        if not opens:
            return signals
        by_gid: dict[str, list[Signal]] = {}
        for sig in opens:
            gid = sig.group_id or sig.symbol
            by_gid.setdefault(gid, []).append(sig)
        if len(by_gid) <= max_n:
            return signals
        ranked = sorted(
            by_gid.items(),
            key=lambda item: -max(float(s.score) for s in item[1]),
        )
        kept: list[Signal] = []
        for _, group_sigs in ranked[:max_n]:
            kept.extend(group_sigs)
        return other + kept

    def _fetch_weights(self) -> dict[str, float]:
        """Return the latest per-symbol volume cache, or ``{}`` when missing."""
        provider = self._weight_provider
        if provider is None:
            return {}
        try:
            data = provider()
        except Exception:  # noqa: BLE001 — never let weight fetch crash the loop
            logger.debug("weight provider raised; using equal weights", exc_info=True)
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _compute_reference(
        self,
        bases: dict[str, float],
        weights_by_symbol: dict[str, float],
    ) -> float:
        """Return the reference basis for deviation (mode from settings)."""
        mode = (self._settings.pair_reference_mode or "btc_anchor").strip().lower()
        if mode == "independent":
            return 0.0
        if mode == "btc_anchor":
            if _BTC_PAIR_KEY in bases:
                return bases[_BTC_PAIR_KEY]
            if _ETH_PAIR_KEY in bases:
                return bases[_ETH_PAIR_KEY]
            return sum(bases.values()) / max(1, len(bases))
        # ``weighted`` — volume-weighted mean across observed pairs.
        total_weight = 0.0
        weighted_sum = 0.0
        for pair in self._pairs:
            key = self._key(pair)
            basis = bases.get(key)
            if basis is None:
                continue
            usdt_vol = weights_by_symbol.get(pair.usdt_symbol, 0.0)
            usdc_vol = weights_by_symbol.get(pair.usdc_symbol, 0.0)
            weight = max(0.0, float(usdt_vol)) + max(0.0, float(usdc_vol)) + 1.0
            total_weight += weight
            weighted_sum += weight * basis
        if total_weight <= 0:
            return sum(bases.values()) / max(1, len(bases))
        return weighted_sum / total_weight

    def _maybe_log_reference(
        self,
        now: float,
        reference: float,
        bases: dict[str, float],
        weights_by_symbol: dict[str, float],
    ) -> None:
        if now - self._last_reference_log_ts < _REFERENCE_LOG_INTERVAL_SEC:
            return
        self._last_reference_log_ts = now
        weight_bits: list[str] = []
        for pair in self._pairs:
            key = self._key(pair)
            if key not in bases:
                continue
            base = pair.usdt_symbol.removesuffix("USDT")
            usdt_vol = weights_by_symbol.get(pair.usdt_symbol, 0.0)
            usdc_vol = weights_by_symbol.get(pair.usdc_symbol, 0.0)
            weight_bits.append(f"{base}:{usdt_vol + usdc_vol:.0f}")
        logger.info(
            "[pairs] reference=%.6f n_coins=%d mode=%s weights={%s}",
            reference,
            len(bases),
            (self._settings.pair_reference_mode or "btc_anchor"),
            ", ".join(weight_bits) if weight_bits else "equal",
        )

    def on_fill(
        self,
        symbol: str,
        qty: float,
        side: str,
        *,
        price: float | None = None,
    ) -> None:
        now = time.time()
        for pair in self._pairs:
            key = self._key(pair)
            stats = self._stats.get(key)
            if stats is None:
                continue
            if stats.pending_fills_remaining <= 0:
                continue
            if symbol not in (stats.pending_usdt, stats.pending_usdc):
                continue

            expected = self._expected_side_for_symbol(stats, symbol)
            if expected is None or expected.value.lower() != side.lower():
                continue

            stats.pending_fills_remaining -= 1
            if stats.pending_fills_remaining == 1:
                stats.pending_filled_symbol = symbol
            if price is not None and price > 0:
                if symbol == stats.pending_usdt:
                    stats.pending_entry_usdt_mid, stats.pending_entry_usdt_qty = (
                        apply_attributed_fill_vwap(
                            fill_vwap=stats.pending_entry_usdt_mid,
                            fill_qty_abs=stats.pending_entry_usdt_qty,
                            fill_price=price,
                            fill_qty=qty,
                        )
                    )
                elif symbol == stats.pending_usdc:
                    stats.pending_entry_usdc_mid, stats.pending_entry_usdc_qty = (
                        apply_attributed_fill_vwap(
                            fill_vwap=stats.pending_entry_usdc_mid,
                            fill_qty_abs=stats.pending_entry_usdc_qty,
                            fill_price=price,
                            fill_qty=qty,
                        )
                    )
            if stats.pending_fills_remaining > 0:
                continue

            # Both legs filled: finalize state transition.
            if stats.pending_is_close:
                stats.open_side = 0
                stats.open_qty = 0.0
                stats.open_ts = 0.0
                stats.open_usdt_mid = 0.0
                stats.open_usdc_mid = 0.0
            else:
                stats.open_side = stats.pending_side
                stats.open_qty = stats.pending_qty
                stats.open_ts = now
                if stats.pending_entry_usdt_mid > 0:
                    stats.open_usdt_mid = stats.pending_entry_usdt_mid
                if stats.pending_entry_usdc_mid > 0:
                    stats.open_usdc_mid = stats.pending_entry_usdc_mid

            stats.pending_side = 0
            stats.pending_qty = 0.0
            stats.pending_usdt = ""
            stats.pending_usdc = ""
            stats.pending_since_ts = 0.0
            stats.pending_is_close = False
            stats.pending_filled_symbol = ""
            stats.pending_entry_usdt_mid = 0.0
            stats.pending_entry_usdc_mid = 0.0
            stats.pending_entry_usdt_qty = 0.0
            stats.pending_entry_usdc_qty = 0.0

    # --- Read-only ---

    def reference_basis(self) -> float | None:
        return self._reference_history[-1] if self._reference_history else None

    # --- Internals ---

    def _clear_pending(self, stats: _DeviationStats) -> None:
        stats.pending_side = 0
        stats.pending_qty = 0.0
        stats.pending_usdt = ""
        stats.pending_usdc = ""
        stats.pending_fills_remaining = 0
        stats.pending_since_ts = 0.0
        stats.pending_is_close = False
        stats.pending_filled_symbol = ""
        stats.pending_entry_usdt_mid = 0.0
        stats.pending_entry_usdc_mid = 0.0
        stats.pending_entry_usdt_qty = 0.0
        stats.pending_entry_usdc_qty = 0.0

    def _check_partial_pending(
        self,
        pair: PairConfig,
        stats: _DeviationStats,
        now: float,
    ) -> list[Signal]:
        """Unwind a single filled leg when the partner never arrives."""
        if stats.pending_fills_remaining != 1 or stats.pending_is_close:
            return []
        abort_sec = float(self._settings.pair_partial_fill_abort_sec)
        if abort_sec <= 0 or now - stats.pending_since_ts < abort_sec:
            return []
        filled = stats.pending_filled_symbol
        if not filled:
            self._clear_pending(stats)
            return []
        open_side = self._expected_side_for_symbol(stats, filled)
        if open_side is None:
            self._clear_pending(stats)
            return []
        qty = stats.pending_qty if stats.pending_qty > 0 else self._FALLBACK_QTY
        signal_log_emit(
            logger,
            f"PAIRS partial abort -> {open_side.opposite.value.upper()} {filled} "
            f"qty={qty:.8f} (partner leg never filled)",
            reason="pairs_partial_abort",
        )
        self._clear_pending(stats)
        stats.last_action_ts = now
        return [
            Signal(
                symbol=filled,
                side=open_side.opposite,
                qty=qty,
                reason="pairs_partial_abort",
                reduce_only=True,
            ),
        ]

    def _evaluate(
        self,
        pair: PairConfig,
        stats: _DeviationStats,
        z: float,
        basis: float,
        reference: float,
        usdt_mid: float,
        usdc_mid: float,
    ) -> Iterable[Signal]:
        now = time.time()
        ref_mode = (self._settings.pair_reference_mode or "btc_anchor").strip().lower()
        use_reference = ref_mode != "independent"

        # Pending order state: wait for fills (or time out) before emitting more.
        if stats.pending_fills_remaining > 0:
            if now - stats.pending_since_ts > float(self._settings.pair_pending_timeout_sec):
                # Defensive: if we never got fills (disconnect/reject), allow the
                # strategy to recover and try again later.
                self._clear_pending(stats)
                stats.last_action_ts = now
            else:
                return []

        if now - stats.last_action_ts < float(self._settings.pair_cooldown_sec):
            return []

        max_hold = float(getattr(self._settings, "pair_max_hold_sec", 0) or 0)
        if (
            max_hold > 0
            and stats.open_side != 0
            and stats.open_ts > 0
            and now - stats.open_ts > max_hold
        ):
            return self._emit_unwind(
                pair, stats, z, basis, reference, usdt_mid, usdc_mid, now,
                reason_tag="pairs_time",
                held_sec=now - stats.open_ts,
                exit_reason="time",
            )

        score = min(abs(z) / 4.0, 1.0)
        urgent_floor = float(self._settings.pair_urgent_score)
        if urgent_floor > 0:
            score = max(score, urgent_floor)
        reason_open = f"pairs_open z={z:.2f} basis={basis:.5f} ref={reference:.5f}"
        gid = self._key(pair)

        if stats.open_side == 0:
            if abs(z) < pair.entry_z:
                if abs(z) >= pair.entry_z * 0.85:
                    logger.debug(
                        "PAIRS %s/%s below entry: |z|=%.2f need=%.2f basis=%.5f",
                        pair.usdt_symbol,
                        pair.usdc_symbol,
                        abs(z),
                        pair.entry_z,
                        basis,
                    )
                return []
            qty = self._size_pair(usdt_mid, usdc_mid, abs(z), pair.entry_z)
            if qty <= 0:
                return []

            scale_cap = max(1.0, float(self._settings.pair_size_scale_cap))
            signal = (
                conviction_above_entry(
                    abs(z), entry=pair.entry_z, full=pair.entry_z * scale_cap,
                )
                if pair.entry_z > 0
                else 1.0
            )
            scale = 1.0 + (scale_cap - 1.0) * (signal ** 3)
            notional = qty * max(usdt_mid, usdc_mid)
            dev = basis if not use_reference else basis - reference
            if z >= pair.entry_z:
                stats.pending_side = +1
                stats.pending_qty = qty
                stats.pending_usdt = pair.usdt_symbol
                stats.pending_usdc = pair.usdc_symbol
                stats.pending_fills_remaining = 2
                stats.pending_since_ts = now
                stats.pending_is_close = False
                stats.pending_filled_symbol = ""
                stats.last_action_ts = now
                coin = pair.usdt_symbol.removesuffix("USDT")
                signal_log_emit(
                    logger,
                    f"PAIRS ENTRY {coin} +z={z:.2f} basis={basis:.6f} ref={reference:.6f} "
                    f"deviation={dev:.6f} open_mid_usdt={usdt_mid} "
                    f"open_mid_usdc={usdc_mid} qty={qty:.6f} notional_usd={notional:.2f} "
                    f"scale={scale:.2f}",
                    reason=reason_open,
                )
                return [
                    Signal(symbol=pair.usdc_symbol, side=Side.SELL, qty=qty,
                           reason=reason_open, score=score, group_id=gid),
                    Signal(symbol=pair.usdt_symbol, side=Side.BUY, qty=qty,
                           reason=reason_open, score=score, group_id=gid),
                ]
            # z <= -entry_z
            stats.pending_side = -1
            stats.pending_qty = qty
            stats.pending_usdt = pair.usdt_symbol
            stats.pending_usdc = pair.usdc_symbol
            stats.pending_fills_remaining = 2
            stats.pending_since_ts = now
            stats.pending_is_close = False
            stats.pending_filled_symbol = ""
            stats.last_action_ts = now
            coin = pair.usdt_symbol.removesuffix("USDT")
            signal_log_emit(
                logger,
                f"PAIRS ENTRY {coin} -z={z:.2f} basis={basis:.6f} ref={reference:.6f} "
                f"deviation={dev:.6f} open_mid_usdt={usdt_mid} "
                f"open_mid_usdc={usdc_mid} qty={qty:.6f} notional_usd={notional:.2f} "
                f"scale={scale:.2f}",
                reason=reason_open,
            )
            return [
                Signal(symbol=pair.usdt_symbol, side=Side.SELL, qty=qty,
                       reason=reason_open, score=score, group_id=gid),
                Signal(symbol=pair.usdc_symbol, side=Side.BUY, qty=qty,
                       reason=reason_open, score=score, group_id=gid),
            ]

        # Already in a trade. Two unwind triggers, both in basis space:
        #
        #   converged   : |z| <= exit_z   -> take profit (basis reverted)
        #   diverged    : z past stop_z against open_side -> stop loss
        #
        # Stop wins ties so a slow drift that briefly touches both ends
        # of the gate is treated as the worse outcome.
        diverged = (
            (stats.open_side == +1 and z >= pair.stop_z)
            or (stats.open_side == -1 and z <= -pair.stop_z)
        )
        converged = abs(z) <= pair.exit_z
        if not (diverged or converged):
            return []

        # Avoid instantaneous in/out churn: hold positions for at least a bit
        # unless we're stopping out.
        if not diverged and stats.open_ts > 0:
            if now - stats.open_ts < float(self._settings.pair_min_hold_sec):
                return []

        exit_reason = "stop" if diverged else "close"
        held = now - stats.open_ts if stats.open_ts > 0 else 0.0
        return self._emit_unwind(
            pair, stats, z, basis, reference, usdt_mid, usdc_mid, now,
            reason_tag="pairs_stop" if diverged else "pairs_close",
            held_sec=held,
            exit_reason=exit_reason,
            extend_stop_cooldown=diverged,
        )

    def _emit_unwind(
        self,
        pair: PairConfig,
        stats: _DeviationStats,
        z: float,
        basis: float,
        reference: float,
        usdt_mid: float,
        usdc_mid: float,
        now: float,
        *,
        reason_tag: str,
        held_sec: float = 0.0,
        exit_reason: str = "close",
        extend_stop_cooldown: bool = False,
    ) -> list[Signal]:
        if stats.pending_fills_remaining > 0:
            return []
        gid = self._key(pair)
        reason_text = f"{reason_tag} z={z:.2f}"
        pnl_bps = self._estimate_pnl_bps(stats, usdt_mid, usdc_mid)
        qty = stats.open_qty if stats.open_qty > 0 else self._FALLBACK_QTY
        if stats.open_side == +1:
            unwind_usdc, unwind_usdt = Side.BUY, Side.SELL
        else:
            unwind_usdc, unwind_usdt = Side.SELL, Side.BUY
        stats.pending_side = stats.open_side
        stats.pending_qty = qty
        stats.pending_usdt = pair.usdt_symbol
        stats.pending_usdc = pair.usdc_symbol
        stats.pending_fills_remaining = 2
        stats.pending_since_ts = now
        stats.pending_is_close = True
        if extend_stop_cooldown:
            stop_cd = float(self._settings.pair_stop_cooldown_sec)
            normal_cd = float(self._settings.pair_cooldown_sec)
            stats.last_action_ts = now + max(0.0, stop_cd - normal_cd)
        else:
            stats.last_action_ts = now
        coin = pair.usdt_symbol.removesuffix("USDT")
        if reason_tag == "pairs_time":
            logger.warning(
                "PAIRS time-stop %s held %.0fs > max %.0fs z=%.2f",
                coin,
                held_sec,
                float(self._settings.pair_max_hold_sec),
                z,
            )
        signal_log_emit(
            logger,
            f"PAIRS EXIT {coin} reason={exit_reason} z={z:.2f} held={held_sec:.0f}s "
            f"pnl_bps={pnl_bps:.1f} fill_usdt={pair.usdt_symbol} fill_usdc={pair.usdc_symbol} "
            f"qty={qty:.8f} basis={basis:.6f} ref={reference:.6f}",
            reason=reason_text,
        )
        return [
            Signal(
                symbol=pair.usdc_symbol,
                side=unwind_usdc,
                qty=qty,
                reason=reason_text,
                group_id=gid,
                reduce_only=True,
            ),
            Signal(
                symbol=pair.usdt_symbol,
                side=unwind_usdt,
                qty=qty,
                reason=reason_text,
                group_id=gid,
                reduce_only=True,
            ),
        ]

    def _check_leg_loss_cap(
        self,
        pair: PairConfig,
        stats: _DeviationStats,
        usdt_mid: float,
        usdc_mid: float,
        z: float,
    ) -> list[Signal]:
        """Force-unwind when either leg's absolute PnL exceeds the USD cap."""
        if stats.pending_fills_remaining > 0:
            return []
        max_loss = float(getattr(self._settings, "pair_max_leg_loss_usd", 0) or 0)
        if max_loss <= 0 or stats.open_qty <= 0:
            return []
        if stats.open_usdt_mid <= 0 or stats.open_usdc_mid <= 0:
            return []
        qty = stats.open_qty
        if stats.open_side == +1:
            usdt_pnl = (usdt_mid - stats.open_usdt_mid) * qty
            usdc_pnl = (stats.open_usdc_mid - usdc_mid) * qty
        else:
            usdt_pnl = (stats.open_usdt_mid - usdt_mid) * qty
            usdc_pnl = (usdc_mid - stats.open_usdc_mid) * qty
        if abs(usdt_pnl) <= max_loss and abs(usdc_pnl) <= max_loss:
            return []
        logger.warning(
            "PAIRS leg-loss cap %s usdt_pnl=%.2f usdc_pnl=%.2f max=%.2f",
            pair.usdt_symbol.removesuffix("USDT"),
            usdt_pnl,
            usdc_pnl,
            max_loss,
        )
        return self._emit_unwind(
            pair,
            stats,
            z,
            _basis(usdc_mid, usdt_mid),
            0.0,
            usdt_mid,
            usdc_mid,
            time.time(),
            reason_tag="pairs_leg_loss",
            held_sec=time.time() - stats.open_ts if stats.open_ts > 0 else 0.0,
            exit_reason="leg_loss",
        )

    def _size_pair(
        self,
        usdt_mid: float,
        usdc_mid: float,
        abs_z: float = 0.0,
        entry_z: float = 0.0,
    ) -> float:
        """Return the per-leg base qty for a coin-pair entry (hybrid).

        Two-stage sizing:

        1. **Stop-loss budget (floor):**
           ``target_notional = equity * risk_per_trade_pct / stop_loss_pct``
           sized so a stop-out costs exactly ``risk_per_trade_pct`` of
           equity.

        2. **Cubic conviction scale:** ``qty = P_floor + (P_ceil - P_floor) × s³``
           with ``s = 0`` at ``|z| = entry_z`` (minimum risk size) and
           ``s = 1`` at ``|z| = entry_z × pair_size_scale_cap``.

        The two legs share ``qty`` (same base asset) so the pair stays
        dollar-neutral after sizing.

        Falls back to the legacy ``_FALLBACK_QTY`` only when no equity
        provider is wired (unit tests). Returns 0 if equity is unknown
        or non-positive so the engine skips the entry instead of
        opening a position it can't size.
        """
        if self._equity_provider is None:
            return self._FALLBACK_QTY
        try:
            equity = float(self._equity_provider())
        except Exception:  # noqa: BLE001
            logger.exception("equity provider raised; skipping pair sizing")
            return 0.0
        if equity <= 0:
            return 0.0
        stop_pct = self._settings.default_stop_loss_pct
        risk_pct = self._settings.risk_per_trade_pct
        if stop_pct <= 0 or risk_pct <= 0:
            return 0.0
        target_notional = (equity * risk_pct) / stop_pct
        # Pick the higher mid as the divisor so the resulting qty
        # produces ~target_notional on whichever leg is more expensive
        # (the binding venue-min constraint sits on that leg too).
        ref_mid = max(usdt_mid, usdc_mid)
        if ref_mid <= 0:
            return 0.0

        p_floor = target_notional / ref_mid
        scale_cap = max(1.0, float(self._settings.pair_size_scale_cap))
        p_ceil = p_floor * scale_cap
        if entry_z > 0 and abs_z > 0:
            signal = conviction_above_entry(
                abs_z, entry=entry_z, full=entry_z * scale_cap,
            )
        else:
            signal = 0.0

        qty = cubic_scaled_qty(p_floor, signal, p_ceil=p_ceil)
        max_leg_notional = float(
            getattr(self._settings, "pair_max_leg_notional", 0) or 0,
        )
        if max_leg_notional > 0:
            qty = min(qty, max_leg_notional / ref_mid)
        return qty

    @staticmethod
    def _estimate_pnl_bps(
        stats: _DeviationStats,
        usdt_mid: float,
        usdc_mid: float,
    ) -> float:
        """Approximate combined leg PnL in basis points of entry notional."""
        if stats.open_qty <= 0 or stats.open_usdt_mid <= 0 or stats.open_usdc_mid <= 0:
            return 0.0
        qty = stats.open_qty
        if stats.open_side == +1:
            usdt_pnl = (usdt_mid - stats.open_usdt_mid) * qty
            usdc_pnl = (stats.open_usdc_mid - usdc_mid) * qty
        elif stats.open_side == -1:
            usdt_pnl = (stats.open_usdt_mid - usdt_mid) * qty
            usdc_pnl = (usdc_mid - stats.open_usdc_mid) * qty
        else:
            return 0.0
        notional = qty * (stats.open_usdt_mid + stats.open_usdc_mid) / 2.0
        if notional <= 0:
            return 0.0
        return (usdt_pnl + usdc_pnl) / notional * 10_000.0

    @staticmethod
    def _key(pair: PairConfig) -> str:
        return f"{pair.usdt_symbol}|{pair.usdc_symbol}"

    @staticmethod
    def _expected_side_for_symbol(stats: _DeviationStats, symbol: str) -> Side | None:
        # When opening:
        #   open_side = +1 => SELL USDC, BUY USDT
        #   open_side = -1 => SELL USDT, BUY USDC
        #
        # When closing we emit the opposite of the open legs; pending_is_close
        # indicates this is a close.
        if stats.pending_side == 0:
            return None

        if stats.pending_is_close:
            if stats.pending_side == +1:
                return Side.BUY if symbol == stats.pending_usdc else Side.SELL
            return Side.SELL if symbol == stats.pending_usdc else Side.BUY

        if stats.pending_side == +1:
            return Side.SELL if symbol == stats.pending_usdc else Side.BUY
        return Side.BUY if symbol == stats.pending_usdc else Side.SELL


def _basis(usdc_mid: float, usdt_mid: float) -> float:
    """Per-coin implied log(USDC/USDT). Positive => USDC leg richer than USDT."""
    return math.log(usdc_mid) - math.log(usdt_mid)
