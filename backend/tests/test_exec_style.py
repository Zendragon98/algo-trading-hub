"""Execution style resolution (cross-touch vs passive VWAP)."""

from __future__ import annotations

from common.config import Settings
from common.enums import Urgency
from engine.execution.exec_style import resolve_cross_touch


def test_flow_aggressive_entry_crosses_touch() -> None:
    s = Settings.model_validate({"flow_entry_cross_touch": True})
    assert resolve_cross_touch(
        s,
        strategy_name="flow_momentum",
        urgency=Urgency.AGGRESSIVE,
        reduce_only=False,
        notes="flow_momentum_enter",
    )


def test_flow_passive_entry_stays_passive() -> None:
    s = Settings.model_validate({"flow_entry_cross_touch": True})
    assert not resolve_cross_touch(
        s,
        strategy_name="flow_momentum",
        urgency=Urgency.PASSIVE,
        reduce_only=False,
        notes="flow_momentum_enter",
    )


def test_sma_aggressive_respects_global_toggle() -> None:
    s = Settings.model_validate({"urgent_cross_touch": False})
    assert not resolve_cross_touch(
        s,
        strategy_name="sma_crossover",
        urgency=Urgency.AGGRESSIVE,
        reduce_only=False,
        notes="sma_cross_up",
    )
    s_on = Settings.model_validate({"urgent_cross_touch": True})
    assert resolve_cross_touch(
        s_on,
        strategy_name="sma_crossover",
        urgency=Urgency.AGGRESSIVE,
        reduce_only=False,
        notes="sma_cross_up",
    )
