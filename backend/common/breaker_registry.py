"""Canonical circuit-breaker definitions for settings, API, and UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BreakerSeverityLabel = Literal["minor", "major"]
BreakerScopeLabel = Literal["engine", "symbol", "parent"]
BreakerGroup = Literal[
    "market_data",
    "execution",
    "portfolio",
    "reconciliation",
    "market_making",
    "operator",
]


@dataclass(frozen=True, slots=True)
class BreakerDefinition:
    code: str
    severity: BreakerSeverityLabel
    scope: BreakerScopeLabel
    label: str
    description: str
    group: BreakerGroup
    default_enabled: bool = True
    disableable: bool = True


BREAKER_REGISTRY: tuple[BreakerDefinition, ...] = (
    BreakerDefinition(
        "stale_tick",
        "minor",
        "symbol",
        "Stale tick",
        "Veto new entries when last trade tick is older than max_tick_age_sec.",
        "market_data",
    ),
    BreakerDefinition(
        "wide_spread",
        "minor",
        "symbol",
        "Wide spread",
        "Veto entries when quoted spread exceeds static or EWMA dynamic threshold.",
        "market_data",
    ),
    BreakerDefinition(
        "stale_market_data",
        "minor",
        "engine",
        "Stale market WS",
        "Pause new orders when the public market-data WebSocket is silent.",
        "market_data",
    ),
    BreakerDefinition(
        "stale_user_data",
        "minor",
        "engine",
        "Stale user stream",
        "Pause new orders when user-data WS is stale while orders are working.",
        "market_data",
    ),
    BreakerDefinition(
        "md_crossed_book",
        "minor",
        "symbol",
        "Crossed book",
        "Block symbol when local book shows bid above ask.",
        "market_data",
    ),
    BreakerDefinition(
        "repeat_reject",
        "minor",
        "symbol",
        "Repeat rejects",
        "Pause symbol after max_consecutive_rejects venue rejections.",
        "execution",
    ),
    BreakerDefinition(
        "slippage_breach",
        "minor",
        "parent",
        "Slippage breach",
        "Cancel parent when fill slippage exceeds max_slippage_bps.",
        "execution",
    ),
    BreakerDefinition(
        "exec_quality",
        "major",
        "engine",
        "Execution quality",
        "Latch and flatten when rolling average slippage exceeds kill threshold.",
        "execution",
    ),
    BreakerDefinition(
        "max_drawdown",
        "major",
        "engine",
        "Max drawdown",
        "Latch and flatten when session drawdown exceeds max_drawdown_pct.",
        "portfolio",
    ),
    BreakerDefinition(
        "hwm_drawdown",
        "major",
        "engine",
        "HWM drawdown",
        "Latch and flatten when drawdown from equity high-water mark exceeds limit.",
        "portfolio",
    ),
    BreakerDefinition(
        "daily_loss",
        "major",
        "engine",
        "Daily loss",
        "Latch and flatten when daily realised loss exceeds daily_loss_kill_pct.",
        "portfolio",
    ),
    BreakerDefinition(
        "consecutive_losses",
        "major",
        "engine",
        "Consecutive losses",
        "Latch and flatten after max_consecutive_losses losing closes.",
        "portfolio",
    ),
    BreakerDefinition(
        "reconcile_mismatch",
        "major",
        "engine",
        "Position reconcile",
        "Latch and flatten when venue position qty differs from local OMS.",
        "reconciliation",
    ),
    BreakerDefinition(
        "order_reconcile_mismatch",
        "minor",
        "engine",
        "Order reconcile",
        "Pause when open orders on venue do not match local OMS.",
        "reconciliation",
    ),
    BreakerDefinition(
        "group_unwind_failed",
        "major",
        "symbol",
        "Group unwind failed",
        "Latch symbol when compensating unwind after a failed group exit fails.",
        "reconciliation",
    ),
    BreakerDefinition(
        "price_jump",
        "minor",
        "symbol",
        "Price jump",
        "Veto MM entries when short-horizon return jump is active.",
        "market_making",
    ),
    BreakerDefinition(
        "toxic_flow",
        "minor",
        "symbol",
        "Toxic flow",
        "Veto MM entries when tape toxicity score exceeds threshold.",
        "market_making",
    ),
    BreakerDefinition(
        "book_depleted",
        "minor",
        "symbol",
        "Book depleted",
        "Veto MM entries when bid/ask depth ratio is one-sided.",
        "market_making",
    ),
    BreakerDefinition(
        "operator_halt",
        "major",
        "engine",
        "Operator halt",
        "Manual trading halt from the control API (Halt button).",
        "operator",
        disableable=False,
    ),
)

BREAKER_CODES: frozenset[str] = frozenset(d.code for d in BREAKER_REGISTRY)
BREAKER_CODES_DISABLEABLE: frozenset[str] = frozenset(
    d.code for d in BREAKER_REGISTRY if d.disableable
)
MM_ONLY_BREAKER_CODES: frozenset[str] = frozenset(
    d.code for d in BREAKER_REGISTRY if d.group == "market_making"
)
MM_STRATEGY_NAMES: frozenset[str] = frozenset({"market_making_v2"})
# Symbol-scope trips that should not block unrelated strategies.
STRATEGY_SCOPED_BREAKER_CODES: frozenset[str] = MM_ONLY_BREAKER_CODES | frozenset({
    "repeat_reject",
    "group_unwind_failed",
})
MAJOR_BREAKER_CODES: frozenset[str] = frozenset(
    d.code for d in BREAKER_REGISTRY if d.severity == "major"
)

LIVE_DISABLE_CONFIRM_TOKEN = "DISABLE LIVE BREAKERS"


def breaker_applies_to_strategy(code: str, strategy_name: str) -> bool:
    """Return False when a strategy-scoped breaker should not gate ``strategy_name``.

    Used as a fallback when an active breach has no ``strategy_name`` attribution
    (e.g. legacy trips). Attributed breaches compare ``strategy_name`` directly.
    """
    if code in MM_ONLY_BREAKER_CODES:
        return strategy_name in MM_STRATEGY_NAMES
    if code == "repeat_reject":
        return bool(strategy_name)
    if code == "group_unwind_failed":
        return strategy_name == "pairs_trading_usdt_usdc"
    return True


def default_breaker_enabled_map() -> dict[str, bool]:
    return {d.code: d.default_enabled for d in BREAKER_REGISTRY}


def merge_breaker_enabled(
    patch: dict[str, bool] | None,
    *,
    base: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Merge ``patch`` into defaults; reject unknown codes."""
    out = dict(base or default_breaker_enabled_map())
    if not patch:
        return out
    unknown = set(patch) - BREAKER_CODES
    if unknown:
        msg = ", ".join(sorted(unknown))
        raise ValueError(f"unknown breaker code(s): {msg}")
    for code, enabled in patch.items():
        if code == "operator_halt" and not enabled:
            continue
        out[code] = bool(enabled)
    return out


def majors_being_disabled(
    current: dict[str, bool],
    patch: dict[str, bool],
) -> list[str]:
    """Return major codes that would flip from enabled to disabled."""
    disabled: list[str] = []
    for code in MAJOR_BREAKER_CODES:
        if code not in patch:
            continue
        if current.get(code, True) and not patch[code]:
            disabled.append(code)
    return disabled
