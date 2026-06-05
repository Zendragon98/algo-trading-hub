"""Strategy attribution helpers for trades and flatten paths."""

from __future__ import annotations

import pytest

from engine.performance.strategy_attribution import FLATTEN_STRATEGY, split_pnl_by_strategy


def test_split_pnl_flatten_bucket() -> None:
    split = split_pnl_by_strategy(-12.5, FLATTEN_STRATEGY, None)
    assert split == {FLATTEN_STRATEGY: pytest.approx(-12.5)}
