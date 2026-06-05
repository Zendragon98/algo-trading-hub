from __future__ import annotations

import pytest

from engine.strategies.signal_scaling import (
    clamp_unit_signal,
    conviction_above_entry,
    cubic_position_size,
    cubic_scaled_qty,
    normalized_unit_signal,
)


def test_clamp_unit_signal() -> None:
    assert clamp_unit_signal(0.5) == 0.5
    assert clamp_unit_signal(1.5) == 1.0
    assert clamp_unit_signal(-2.0) == -1.0


def test_cubic_scaled_qty_hits_floor_at_zero_signal() -> None:
    assert cubic_scaled_qty(100.0, 0.0) == 100.0
    assert cubic_scaled_qty(100.0, 0.0, p_ceil=200.0) == 100.0


def test_cubic_scaled_qty_grows_to_ceiling() -> None:
    assert cubic_scaled_qty(100.0, 1.0, p_ceil=200.0) == 200.0
    assert cubic_scaled_qty(100.0, 0.5, p_ceil=200.0) == pytest.approx(112.5)


def test_conviction_above_entry() -> None:
    assert conviction_above_entry(2.0, entry=2.0, full=4.0) == 0.0
    assert conviction_above_entry(4.0, entry=2.0, full=4.0) == 1.0
    assert conviction_above_entry(3.0, entry=2.0, full=4.0) == 0.5


def test_cubic_position_size_examples() -> None:
    p_max = 100.0
    assert cubic_position_size(p_max, 1.0) == 100.0
    assert cubic_position_size(p_max, -1.0) == 100.0
    assert cubic_position_size(p_max, 0.0) == 0.0
    assert cubic_position_size(p_max, 0.5) == pytest.approx(12.5)


def test_normalized_unit_signal() -> None:
    assert normalized_unit_signal(0.5, full_scale=1.0) == 0.5
    assert normalized_unit_signal(2.0, full_scale=1.0) == 1.0
    assert normalized_unit_signal(-0.25, full_scale=0.5) == -0.5
