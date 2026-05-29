"""mm_execution helpers."""

from __future__ import annotations

import pytest

from common.config import Settings
from common.enums import MmExecutionMode, Side
from engine.execution.mm_execution import (
    clamp_targets_no_cross,
    climb_next_price,
    ladder_level_targets,
    parse_ladder_weights,
    resolve_execution_mode,
)


def test_clamp_no_cross_bid() -> None:
    bid, ask = clamp_targets_no_cross(
        101.0, 102.0, best_bid=100.0, best_ask=100.5, tick=0.1,
    )
    assert bid == pytest.approx(100.4)
    assert ask == 102.0


def test_climb_steps_toward_target() -> None:
    nxt = climb_next_price(Side.BUY, 100.0, 100.5, tick=0.1, climb_ticks=1)
    assert nxt == pytest.approx(100.1)


def test_ladder_levels_bid() -> None:
    levels = ladder_level_targets(
        Side.BUY, 100.0, 1.0, tick=0.1, levels=3, spacing_ticks=1, weights=[1, 1, 1],
    )
    assert len(levels) == 3
    assert levels[0].price == pytest.approx(100.0)
    assert levels[1].price == pytest.approx(99.9)


def test_resolve_take_flag() -> None:
    mode = resolve_execution_mode(
        MmExecutionMode.MAKE, Settings(), side=Side.BUY, take_flag=True,
    )
    assert mode is MmExecutionMode.TAKE


def test_parse_ladder_weights_equal() -> None:
    w = parse_ladder_weights("equal", 3)
    assert len(w) == 3
    assert sum(w) == pytest.approx(1.0)
