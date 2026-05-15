from __future__ import annotations

from common.enums import Side
from engine.strategies.position_sync import plan_directional_signal, side_from_qty


def test_plan_close_before_open_on_flip() -> None:
    sig = plan_directional_signal(
        symbol="BTCUSDT",
        target_side=-1,
        entry_qty=0.01,
        position_qty=0.05,
        reason_open="open",
        reason_close="close",
    )
    assert sig is not None
    assert sig.reduce_only is True
    assert sig.side is Side.SELL
    assert sig.qty == 0.05


def test_plan_open_when_flat() -> None:
    sig = plan_directional_signal(
        symbol="BTCUSDT",
        target_side=+1,
        entry_qty=0.01,
        position_qty=0.0,
        reason_open="open",
        reason_close="close",
    )
    assert sig is not None
    assert sig.reduce_only is False
    assert sig.side is Side.BUY
    assert sig.qty == 0.01


def test_side_from_qty() -> None:
    assert side_from_qty(1.0) == 1
    assert side_from_qty(-2.0) == -1
    assert side_from_qty(0.0) == 0
