"""Cross-coin USDT/USDC basis trading.

The Binance Futures Testnet doesn't list a tradable USDT/USDC perp, but
every BTC, ETH, SOL ... pair quoted in *both* stables gives us an
*implied* USDT/USDC rate:

    BTC = BTCUSDT * USDT = BTCUSDC * USDC
    =>  USDT / USDC  =  BTCUSDC / BTCUSDT
    =>  log(USDT/USDC)  =  log(BTCUSDC) - log(BTCUSDT)

We call this the per-coin "basis". Pooling the basis across every
configured pair gives a reference rate that captures the actual
USDT/USDC movement (the direction the user wants to track). Each
individual coin's deviation from that consensus is the trade: when a
coin's USDC leg trades unusually rich relative to peers, short USDC and
long USDT — and vice versa.

    basis_i      = log(coin_USDC) - log(coin_USDT)
    reference    = SUM_i (w_i * basis_i) / SUM_i w_i
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
the *floor* qty, then we scale linearly with ``|z|/entry_z`` above the
threshold (capped at ``pair_size_scale_cap``). Bigger deviations =
bigger conviction => bigger size. Both legs of a coin pair share
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
from common.logging import signal_log
from common.types import Signal

from ..market_data.feature_store import Features
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)


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
        "last_action_ts",
        "pending_side",
        "pending_qty",
        "pending_usdt",
        "pending_usdc",
        "pending_fills_remaining",
        "pending_since_ts",
        "pending_is_close",
        "pending_filled_symbol",
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
        self._stats: dict[str, _DeviationStats] = {
            self._key(p): _DeviationStats(window_sec=p.z_window_sec, min_samples=min_samples)
            for p in self._pairs
        }
        # Reference series so we can log how far the cross-coin consensus
        # has drifted; useful when calibrating thresholds offline.
        self._reference_history: deque[float] = deque(maxlen=600)

        self._load_calibration(settings)

        if not self._pairs:
            logger.warning(
                "PairsTradingStrategy enabled but no USDT/USDC pairs found in SYMBOLS"
            )
        elif len(self._pairs) < 2:
            logger.warning(
                "Pairs trading needs >=2 matched USDT/USDC bases for a consensus "
                "(got %d). Set SYMBOLS=AUTO or add more pairs.",
                len(self._pairs),
            )

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
        new_stats: dict[str, _DeviationStats] = {}
        for p in self._pairs:
            key = self._key(p)
            prev = self._stats.get(key)
            if prev is not None and prev.window_sec == p.z_window_sec:
                new_stats[key] = prev
            else:
                new_stats[key] = _DeviationStats(
                    window_sec=p.z_window_sec,
                    min_samples=int(settings.pair_min_z_samples),
                )
        self._stats = new_stats
        self._load_calibration(settings)

    def _load_calibration(self, settings: Settings) -> None:
        """Apply per-base entry/exit z from ``analytics.pair_analyzer`` JSON."""
        path_str = (settings.pair_calibration_path or "").strip()
        if not path_str:
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
            return

        cal: dict[str, tuple[float, float]] = {}
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
                cal[base] = (
                    float(data["suggested_entry_z"]),
                    float(data["suggested_exit_z"]),
                )
            except (KeyError, TypeError, ValueError):
                logger.warning("invalid pair calibration payload in %s", fpath)
                continue

        for pair in self._pairs:
            base = pair.usdt_symbol.removesuffix("USDT").upper()
            thresholds = cal.get(base)
            if thresholds is None:
                continue
            pair.entry_z, pair.exit_z = thresholds
            logger.info(
                "pairs calibration %s: entry_z=%.2f exit_z=%.2f",
                base, pair.entry_z, pair.exit_z,
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

        if len(bases) < 2:
            # Need at least two coins to form a meaningful consensus.
            # With one coin the deviation collapses to zero by definition.
            return []

        # 2. Reference = volume-weighted mean across observed coins. We
        #    use 24h notional volume on the venue as a market-cap proxy
        #    so liquid coins (BTC, ETH) anchor the consensus and a
        #    micro-cap basis blowing up doesn't drag the reference. The
        #    weight cache is refreshed by the engine on its own cadence;
        #    when empty we fall back to equal weights so cold-start
        #    behaviour matches the unweighted strategy.
        weights_by_symbol = self._fetch_weights()
        reference = self._compute_reference(bases, weights_by_symbol)
        self._reference_history.append(reference)

        # 3. Per-pair deviation z-score and signal generation.
        signals: list[Signal] = []
        for pair in self._pairs:
            key = self._key(pair)
            basis = bases.get(key)
            if basis is None:
                continue
            deviation = basis - reference
            stats = self._stats[key]
            stats.push(now, deviation)
            z = stats.zscore(deviation)
            if z is None:
                continue

            usdt_mid, usdc_mid = mids_by_pair[key]
            signals.extend(
                self._check_partial_pending(pair, stats, now),
            )
            signals.extend(
                self._evaluate(pair, stats, z, basis, reference, usdt_mid, usdc_mid)
            )
        return self._cap_new_entries(signals)

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
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _compute_reference(
        self,
        bases: dict[str, float],
        weights_by_symbol: dict[str, float],
    ) -> float:
        """Return the volume-weighted mean basis across observed pairs.

        Per-pair weight is the sum of the USDT + USDC leg volumes —
        rewarding pairs whose *total* venue activity is highest. Missing
        symbols default to a small positive weight so a not-yet-cached
        pair still contributes (just less than the liquid majors).
        """
        total_weight = 0.0
        weighted_sum = 0.0
        for pair in self._pairs:
            key = self._key(pair)
            basis = bases.get(key)
            if basis is None:
                continue
            usdt_vol = weights_by_symbol.get(pair.usdt_symbol, 0.0)
            usdc_vol = weights_by_symbol.get(pair.usdc_symbol, 0.0)
            # ``+ 1.0`` floor keeps every observed pair in the consensus
            # even before the volume cache lands, while still letting a
            # well-known liquid pair dominate by orders of magnitude.
            weight = max(0.0, float(usdt_vol)) + max(0.0, float(usdc_vol)) + 1.0
            total_weight += weight
            weighted_sum += weight * basis
        if total_weight <= 0:
            # Defensive: should never trigger because the +1.0 floor
            # guarantees positivity, but keeps the divide safe.
            return sum(bases.values()) / max(1, len(bases))
        return weighted_sum / total_weight

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        # Strategy fill hook is best-effort and must remain cheap.
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
            if stats.pending_fills_remaining > 0:
                continue

            # Both legs filled: finalize state transition.
            if stats.pending_is_close:
                stats.open_side = 0
                stats.open_qty = 0.0
                stats.open_ts = 0.0
            else:
                stats.open_side = stats.pending_side
                stats.open_qty = stats.pending_qty
                stats.open_ts = now

            stats.pending_side = 0
            stats.pending_qty = 0.0
            stats.pending_usdt = ""
            stats.pending_usdc = ""
            stats.pending_since_ts = 0.0
            stats.pending_is_close = False
            stats.pending_filled_symbol = ""

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
        signal_log(
            logger,
            f"PAIRS partial abort -> {open_side.opposite.value.upper()} {filled} "
            f"qty={qty:.8f} (partner leg never filled)",
        )
        self._clear_pending(stats)
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

        # Pending order state: wait for fills (or time out) before emitting more.
        if stats.pending_fills_remaining > 0:
            if now - stats.pending_since_ts > float(self._settings.pair_pending_timeout_sec):
                # Defensive: if we never got fills (disconnect/reject), allow the
                # strategy to recover and try again later.
                self._clear_pending(stats)
            else:
                return []

        if now - stats.last_action_ts < float(self._settings.pair_cooldown_sec):
            return []

        score = min(abs(z) / 4.0, 1.0)
        urgent_floor = float(self._settings.pair_urgent_score)
        if urgent_floor > 0:
            score = max(score, urgent_floor)
        reason_open = f"pairs_open z={z:.2f} basis={basis:.5f} ref={reference:.5f}"
        gid = self._key(pair)

        if stats.open_side == 0:
            if abs(z) < pair.entry_z:
                return []
            qty = self._size_pair(usdt_mid, usdc_mid, abs(z), pair.entry_z)
            if qty <= 0:
                return []

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
                signal_log(
                    logger,
                    f"PAIRS entry +z={z:.2f} short {pair.usdc_symbol}, "
                    f"long {pair.usdt_symbol} qty={qty:.8f}",
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
            signal_log(
                logger,
                f"PAIRS entry -z={z:.2f} short {pair.usdt_symbol}, "
                f"long {pair.usdc_symbol} qty={qty:.8f}",
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

        unwind_reason = "pairs_stop" if diverged else "pairs_close"
        reason_text = f"{unwind_reason} z={z:.2f}"
        qty = stats.open_qty if stats.open_qty > 0 else self._FALLBACK_QTY
        if stats.open_side == +1:
            # We were short USDC + long USDT. Reverse to flatten.
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
        stats.last_action_ts = now
        signal_log(
            logger,
            f"PAIRS {unwind_reason} z={z:.2f} -> close "
            f"{pair.usdt_symbol}/{pair.usdc_symbol} qty={qty:.8f}",
        )
        return [
            Signal(symbol=pair.usdc_symbol, side=unwind_usdc, qty=qty,
                   reason=reason_text, group_id=gid),
            Signal(symbol=pair.usdt_symbol, side=unwind_usdt, qty=qty,
                   reason=reason_text, group_id=gid),
        ]

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

        2. **Conviction scale (multiplier):** when ``|z|`` exceeds the
           entry threshold we scale the floor by
           ``min(|z|/entry_z, pair_size_scale_cap)``. ``|z| == entry_z``
           gives a 1.0x trade (the floor); ``|z| == 2 * entry_z`` gives a
           2.0x trade; anything beyond the cap stays clamped so a
           transient z-spike can't blow up the leg notional.

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

        # Hybrid scale: 1.0 at |z|==entry_z, capped at pair_size_scale_cap.
        scale_cap = max(1.0, float(self._settings.pair_size_scale_cap))
        if entry_z > 0 and abs_z > 0:
            scale = min(scale_cap, max(1.0, abs_z / entry_z))
        else:
            scale = 1.0

        return (target_notional / ref_mid) * scale

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
    """Per-coin implied log(USDT/USDC). Positive => USDT richer than USDC."""
    return math.log(usdc_mid) - math.log(usdt_mid)
