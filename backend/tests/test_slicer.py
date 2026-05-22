"""Slicer schedule weights and total qty conservation."""

from __future__ import annotations

import pytest

from common.enums import AlgoMode
from engine.execution.slicer import build_schedule


def _qty_sum(slices) -> float:
    return sum(s.qty for s in slices)


def test_normal_uniform() -> None:
    slices = build_schedule(mode=AlgoMode.NORMAL, total_qty=10.0, duration_sec=60.0, n_slices=5)
    assert len(slices) == 5
    assert pytest.approx(_qty_sum(slices), rel=1e-9) == 10.0
    qtys = [s.qty for s in slices]
    assert all(abs(q - 2.0) < 1e-9 for q in qtys)


def test_frontload_decays() -> None:
    slices = build_schedule(mode=AlgoMode.FRONTLOAD, total_qty=10.0, duration_sec=60.0, n_slices=6)
    assert pytest.approx(_qty_sum(slices), rel=1e-6) == 10.0
    # Strictly decreasing series.
    qtys = [s.qty for s in slices]
    for a, b in zip(qtys, qtys[1:], strict=False):
        assert a > b


def test_backload_grows() -> None:
    slices = build_schedule(mode=AlgoMode.BACKLOAD, total_qty=10.0, duration_sec=60.0, n_slices=6)
    assert pytest.approx(_qty_sum(slices), rel=1e-6) == 10.0
    qtys = [s.qty for s in slices]
    for a, b in zip(qtys, qtys[1:], strict=False):
        assert a < b


def test_validation() -> None:
    with pytest.raises(ValueError):
        build_schedule(mode=AlgoMode.NORMAL, total_qty=0, duration_sec=10, n_slices=2)
    with pytest.raises(ValueError):
        build_schedule(mode=AlgoMode.NORMAL, total_qty=1, duration_sec=10, n_slices=0)
