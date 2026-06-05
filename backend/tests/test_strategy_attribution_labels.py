"""Strategy attribution helpers for trades and flatten paths."""

from __future__ import annotations

import pytest

from engine.performance.strategy_attribution import (
    FLATTEN_STRATEGY,
    NETTED_STRATEGY,
    RISK_EXIT_STRATEGY,
    split_pnl_by_strategy,
    strategy_for_risk_exit,
)


def test_split_pnl_flatten_bucket() -> None:
    split = split_pnl_by_strategy(-12.5, FLATTEN_STRATEGY, None)
    assert split == {FLATTEN_STRATEGY: pytest.approx(-12.5)}


def test_split_pnl_risk_exit_bucket() -> None:
    split = split_pnl_by_strategy(3.0, RISK_EXIT_STRATEGY, None)
    assert split == {RISK_EXIT_STRATEGY: pytest.approx(3.0)}


def test_strategy_for_risk_exit_stop_loss_single_owner() -> None:
    attr = strategy_for_risk_exit(
        symbol="BTCUSDT",
        reason="stop_loss",
        multi_mode=True,
        active_strategy="all",
        ledger_snapshot={"sma_crossover": {"BTCUSDT": 0.05}},
    )
    assert attr.strategy_name == "sma_crossover"
    assert attr.strategy_contributions is None


def test_strategy_for_risk_exit_stop_loss_multi_owner_is_netted() -> None:
    attr = strategy_for_risk_exit(
        symbol="BTCUSDT",
        reason="stop_loss",
        multi_mode=True,
        active_strategy="all",
        ledger_snapshot={
            "flow_momentum": {"BTCUSDT": 0.03},
            "sma_crossover": {"BTCUSDT": 0.05},
        },
    )
    assert attr.strategy_name == NETTED_STRATEGY
    assert attr.strategy_contributions == {
        "flow_momentum": pytest.approx(0.03),
        "sma_crossover": pytest.approx(0.05),
    }


def test_strategy_for_risk_exit_drawdown_kill_is_flatten() -> None:
    attr = strategy_for_risk_exit(
        symbol="ETHUSDT",
        reason="max_drawdown",
        multi_mode=True,
        active_strategy="all",
        ledger_snapshot={},
    )
    assert attr.strategy_name == FLATTEN_STRATEGY
