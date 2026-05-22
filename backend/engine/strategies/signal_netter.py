"""Net ungrouped strategy signals before venue submission.

Pair legs (``group_id`` set) are never netted across strategies or symbols;
they pass through unchanged so atomic multi-leg submits stay intact.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from common.enums import Side
from common.logging import signal_log_emit
from common.types import Signal

logger = logging.getLogger(__name__)


def _signed_delta(sig: Signal) -> float:
    return sig.qty if sig.side is Side.BUY else -sig.qty


@dataclass(slots=True)
class NettedSignal:
    """One venue-bound signal plus per-strategy intended deltas."""

    signal: Signal
    # strategy_name -> signed base-asset delta intended for this symbol
    contributions: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class NettingResult:
    """Output of ``net_strategy_signals``."""

    loose: list[NettedSignal] = field(default_factory=list)
    groups: dict[str, list[Signal]] = field(default_factory=dict)


def net_strategy_signals(
    tagged: list[tuple[str, Signal]],
) -> NettingResult:
    """Collapse opposing single-leg intents on the same symbol.

    Args:
        tagged: ``(strategy_name, signal)`` pairs from every active strategy.

    Returns:
        Netted singles and untouched pair groups (keyed by ``group_id``).
    """
    groups: dict[str, list[Signal]] = defaultdict(list)
    by_symbol: dict[str, list[tuple[str, Signal]]] = defaultdict(list)

    for strategy, sig in tagged:
        tagged_sig = _with_strategy(sig, strategy)
        if sig.group_id:
            groups[sig.group_id].append(tagged_sig)
            continue
        by_symbol[sig.symbol.upper()].append((strategy, tagged_sig))

    loose: list[NettedSignal] = []
    for symbol, entries in by_symbol.items():
        contributions: dict[str, float] = defaultdict(float)
        net = 0.0
        reasons: list[str] = []
        max_score = 0.0
        reduce_only = True
        for strategy, sig in entries:
            delta = _signed_delta(sig)
            contributions[strategy] += delta
            net += delta
            reasons.append(f"{strategy}:{sig.reason}")
            max_score = max(max_score, sig.score)
            reduce_only = reduce_only and sig.reduce_only

        if abs(net) < 1e-12:
            logger.debug(
                "net zero %s: opposing intents cancelled (%s)",
                symbol,
                " | ".join(reasons),
            )
            continue

        side = Side.BUY if net > 0 else Side.SELL
        net_reason = " | ".join(reasons)
        signal_log_emit(
            logger,
            f"net {side.value} {symbol} qty={abs(net):.8f} "
            f"({len(entries)} strategies)",
            reason=net_reason,
        )
        loose.append(
            NettedSignal(
                signal=Signal(
                    symbol=symbol,
                    side=side,
                    qty=abs(net),
                    reason=net_reason,
                    score=max_score,
                    reduce_only=reduce_only,
                    strategy_name="__netted__",
                ),
                contributions=dict(contributions),
            )
        )

    return NettingResult(loose=loose, groups=dict(groups))


def _with_strategy(sig: Signal, strategy: str) -> Signal:
    if sig.strategy_name == strategy:
        return sig
    return Signal(
        symbol=sig.symbol,
        side=sig.side,
        qty=sig.qty,
        reason=sig.reason,
        score=sig.score,
        group_id=sig.group_id,
        reduce_only=sig.reduce_only,
        ts=sig.ts,
        strategy_name=strategy,
    )
