"""Shared per-strategy PnL attribution helpers."""

from __future__ import annotations

from dataclasses import dataclass

NETTED_STRATEGY = "__netted__"
FLATTEN_STRATEGY = "__flatten__"
RISK_EXIT_STRATEGY = "risk_exit"

_PORTFOLIO_KILL_REASONS = frozenset({"max_drawdown", "hwm_drawdown"})


@dataclass(frozen=True, slots=True)
class RiskExitAttribution:
    strategy_name: str
    strategy_contributions: dict[str, float] | None = None


def _ledger_owners(
    symbol: str,
    ledger_snapshot: dict[str, dict[str, float]],
) -> dict[str, float]:
    sym = symbol.upper()
    return {
        strat: float(qty)
        for strat, syms in ledger_snapshot.items()
        for qty in [syms.get(sym, 0.0)]
        if abs(qty) > 1e-12
    }


def strategy_for_risk_exit(
    *,
    symbol: str,
    reason: str,
    multi_mode: bool,
    active_strategy: str,
    ledger_snapshot: dict[str, dict[str, float]],
) -> RiskExitAttribution:
    """Pick parent attribution for risk-monitor / margin exits."""
    if reason in _PORTFOLIO_KILL_REASONS:
        return RiskExitAttribution(FLATTEN_STRATEGY)
    if reason == "margin_ratio":
        return RiskExitAttribution(RISK_EXIT_STRATEGY)

    if multi_mode:
        owners = _ledger_owners(symbol, ledger_snapshot)
        if not owners:
            return RiskExitAttribution(RISK_EXIT_STRATEGY)
        if len(owners) == 1:
            return RiskExitAttribution(next(iter(owners)))
        return RiskExitAttribution(NETTED_STRATEGY, dict(owners))

    return RiskExitAttribution(active_strategy or RISK_EXIT_STRATEGY)


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
    if strategy_name and strategy_name not in (NETTED_STRATEGY, FLATTEN_STRATEGY):
        return {strategy_name: pnl}
    if strategy_name == FLATTEN_STRATEGY:
        return {FLATTEN_STRATEGY: pnl}
    if strategy_name == RISK_EXIT_STRATEGY:
        return {RISK_EXIT_STRATEGY: pnl}
    return {"unknown": pnl}
