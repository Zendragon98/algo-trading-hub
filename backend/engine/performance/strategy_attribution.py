"""Shared per-strategy PnL attribution helpers."""

from __future__ import annotations

NETTED_STRATEGY = "__netted__"


def contribution_weights(contribs: dict[str, float]) -> dict[str, float]:
    total = sum(abs(v) for v in contribs.values())
    if total <= 0:
        return {}
    return {k: abs(v) / total for k, v in contribs.items()}


def split_pnl_by_strategy(
    pnl: float,
    strategy_name: str,
    strategy_contributions: dict[str, float] | None,
) -> dict[str, float]:
    """Attribute one close PnL row to strategy buckets (reporting only)."""
    if strategy_name == NETTED_STRATEGY and strategy_contributions:
        weights = contribution_weights(strategy_contributions)
        if weights:
            return {strat: pnl * weight for strat, weight in weights.items()}
    if strategy_name and strategy_name != NETTED_STRATEGY:
        return {strategy_name: pnl}
    return {"unknown": pnl}
