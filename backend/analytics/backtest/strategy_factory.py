"""Construct backtest-ready strategy instances with providers wired."""

from __future__ import annotations

from common.config import Settings, normalize_strategy_name
from engine.strategies.blended_signals import BlendedSignalsStrategy
from engine.strategies.pairs_trading import PairsTradingStrategy
from engine.strategies.sma_crossover import SmaCrossoverStrategy
from engine.strategies.strategy_base import StrategyBase

from .simulator import FillSimulator

_BACKTEST_STRATEGIES = {
    SmaCrossoverStrategy.name: SmaCrossoverStrategy,
    BlendedSignalsStrategy.name: BlendedSignalsStrategy,
    PairsTradingStrategy.name: PairsTradingStrategy,
}

_ALIASES = {
    "sma": SmaCrossoverStrategy.name,
    "blend": BlendedSignalsStrategy.name,
    "blended": BlendedSignalsStrategy.name,
    "pairs": PairsTradingStrategy.name,
}


def build_strategy(settings: Settings, simulator: FillSimulator) -> StrategyBase:
    name = normalize_strategy_name(settings.strategy)
    name = _ALIASES.get(name, name)
    cls = _BACKTEST_STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"strategy {settings.strategy!r} not supported for backtest")
    strat = cls(settings)
    strat.attach_equity_provider(lambda: simulator.state.equity_curve[-1] if simulator.state.equity_curve else simulator.state.cash)
    strat.attach_position_provider(simulator.state.qty)
    if isinstance(strat, PairsTradingStrategy):
        weights: dict[str, float] = {}
        strat.attach_weight_provider(lambda: weights)
        strat._backtest_weight_cache = weights  # noqa: SLF001 — runner updates in place
    return strat


def symbols_for_strategy(settings: Settings, strategy: StrategyBase) -> list[str]:
    return list(strategy.symbols())
