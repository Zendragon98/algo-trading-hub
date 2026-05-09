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
    reference    = mean(basis_i for all configured pairs)
    deviation_i  = basis_i - reference
    z_i          = (deviation_i - rolling_mean) / rolling_std

    z_i >= +entry_z  ->  SHORT coin_USDC, LONG coin_USDT (USDC leg rich)
    z_i <= -entry_z  ->  LONG  coin_USDC, SHORT coin_USDT (USDT leg rich)
    |z_i| <= exit_z  ->  unwind

Sizing stays simple here (`base_qty`); the risk manager has the final say.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from common.config import Settings
from common.enums import Side
from common.logging import signal_log
from common.types import Signal

from ..market_data.feature_store import Features
from .strategy_base import StrategyBase

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PairConfig:
    """One coin traded in both USDT and USDC."""

    usdt_symbol: str
    usdc_symbol: str
    base_qty: float = 0.001         # e.g. 0.001 BTC; risk manager rescales
    window: int = 600               # rolling samples (~10min at 1Hz)
    entry_z: float = 2.0
    exit_z: float = 0.5


class _DeviationStats:
    """Rolling mean + std of the per-coin deviation from consensus."""

    __slots__ = ("samples", "_sum", "_sumsq", "open_side")

    def __init__(self, window: int) -> None:
        self.samples: deque[float] = deque(maxlen=window)
        self._sum: float = 0.0
        self._sumsq: float = 0.0
        # +1 = short USDC + long USDT (we faded a rich USDC leg)
        # -1 = long  USDC + short USDT (we faded a rich USDT leg)
        #  0 = flat
        self.open_side: int = 0

    def push(self, value: float) -> None:
        if len(self.samples) == self.samples.maxlen:
            evicted = self.samples[0]
            self._sum -= evicted
            self._sumsq -= evicted * evicted
        self.samples.append(value)
        self._sum += value
        self._sumsq += value * value

    def zscore(self, value: float) -> float | None:
        n = len(self.samples)
        if n < 30:
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

    def __init__(self, settings: Settings) -> None:
        self._pairs: list[PairConfig] = [
            PairConfig(usdt_symbol=usdt, usdc_symbol=usdc)
            for usdt, usdc in settings.pair_legs()
        ]
        self._stats: dict[str, _DeviationStats] = {
            self._key(p): _DeviationStats(window=p.window) for p in self._pairs
        }
        # Reference series so we can log how far the cross-coin consensus
        # has drifted; useful when calibrating thresholds offline.
        self._reference_history: deque[float] = deque(maxlen=600)

        if not self._pairs:
            logger.warning(
                "PairsTradingStrategy enabled but no USDT/USDC pairs found in SYMBOLS"
            )

    def symbols(self) -> list[str]:
        out: list[str] = []
        for pair in self._pairs:
            out.extend([pair.usdt_symbol, pair.usdc_symbol])
        return out

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        # 1. Compute per-pair basis. Skip pairs whose mids aren't ready
        #    yet so a cold-start coin doesn't poison the consensus mean.
        bases: dict[str, float] = {}
        for pair in self._pairs:
            usdt = features.get(pair.usdt_symbol)
            usdc = features.get(pair.usdc_symbol)
            if usdt is None or usdc is None:
                continue
            if usdt.mid is None or usdc.mid is None:
                continue
            if usdt.mid <= 0 or usdc.mid <= 0:
                continue
            bases[self._key(pair)] = _basis(usdc.mid, usdt.mid)

        if len(bases) < 2:
            # Need at least two coins to form a meaningful consensus.
            # With one coin the deviation collapses to zero by definition.
            return []

        # 2. Reference = simple mean across observed coins. A median would
        #    be more outlier-robust but the universe is small (a handful
        #    of coins) and means stay differentiable for offline TCA.
        reference = sum(bases.values()) / len(bases)
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
            stats.push(deviation)
            z = stats.zscore(deviation)
            if z is None:
                continue

            signals.extend(self._evaluate(pair, stats, z, basis, reference))
        return signals

    # --- Read-only ---

    def reference_basis(self) -> float | None:
        return self._reference_history[-1] if self._reference_history else None

    # --- Internals ---

    def _evaluate(
        self,
        pair: PairConfig,
        stats: _DeviationStats,
        z: float,
        basis: float,
        reference: float,
    ) -> Iterable[Signal]:
        score = min(abs(z) / 4.0, 1.0)
        reason_open = f"pairs_open z={z:.2f} basis={basis:.5f} ref={reference:.5f}"
        reason_close = f"pairs_close z={z:.2f}"

        if stats.open_side == 0:
            if z >= pair.entry_z:
                # USDC leg unusually rich vs consensus -> short USDC, long USDT.
                stats.open_side = +1
                signal_log(
                    logger,
                    f"PAIRS entry +z={z:.2f} short {pair.usdc_symbol}, long {pair.usdt_symbol}",
                )
                return [
                    Signal(symbol=pair.usdc_symbol, side=Side.SELL, qty=pair.base_qty,
                           reason=reason_open, score=score),
                    Signal(symbol=pair.usdt_symbol, side=Side.BUY, qty=pair.base_qty,
                           reason=reason_open, score=score),
                ]
            if z <= -pair.entry_z:
                # USDT leg unusually rich vs consensus -> short USDT, long USDC.
                stats.open_side = -1
                signal_log(
                    logger,
                    f"PAIRS entry -z={z:.2f} short {pair.usdt_symbol}, long {pair.usdc_symbol}",
                )
                return [
                    Signal(symbol=pair.usdt_symbol, side=Side.SELL, qty=pair.base_qty,
                           reason=reason_open, score=score),
                    Signal(symbol=pair.usdc_symbol, side=Side.BUY, qty=pair.base_qty,
                           reason=reason_open, score=score),
                ]
            return []

        if abs(z) <= pair.exit_z:
            # Convergence -> unwind whichever direction we opened.
            if stats.open_side == +1:
                # We were short USDC + long USDT. Reverse to flatten.
                unwind_usdc, unwind_usdt = Side.BUY, Side.SELL
            else:
                unwind_usdc, unwind_usdt = Side.SELL, Side.BUY
            stats.open_side = 0
            signal_log(
                logger,
                f"PAIRS exit z={z:.2f} -> close {pair.usdt_symbol}/{pair.usdc_symbol}",
            )
            return [
                Signal(symbol=pair.usdc_symbol, side=unwind_usdc, qty=pair.base_qty,
                       reason=reason_close),
                Signal(symbol=pair.usdt_symbol, side=unwind_usdt, qty=pair.base_qty,
                       reason=reason_close),
            ]
        return []

    @staticmethod
    def _key(pair: PairConfig) -> str:
        return f"{pair.usdt_symbol}|{pair.usdc_symbol}"


def _basis(usdc_mid: float, usdt_mid: float) -> float:
    """Per-coin implied log(USDT/USDC). Positive => USDT richer than USDC."""
    return math.log(usdc_mid) - math.log(usdt_mid)
