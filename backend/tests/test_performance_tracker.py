"""Unit tests for ``PerformanceTracker`` KPI aggregation."""

from __future__ import annotations

from typing import Literal
from unittest.mock import MagicMock

import pytest

from common.enums import Side
from common.types import Fill
from engine.performance.fill_classification import FillClassification
from engine.performance.performance_tracker import PerformanceTracker


def _fill(
    side: Side,
    qty: float,
    px: float,
    *,
    idx: int = 0,
    parent_id: str | None = None,
) -> Fill:
    return Fill(
        child_id=f"C-{idx}",
        parent_id=parent_id,
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        price=px,
        fee=0.0,
        fee_asset="USDT",
    )


def _cls(
    pnl: float | None,
    *,
    action: Literal["open", "close"] = "close",
    entry_price: float | None = 100.0,
    exit_price: float | None = 101.0,
) -> FillClassification:
    return FillClassification(
        action=action,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
    )


def test_gross_pnls_and_profit_factor_ignore_opens() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_fill(
        _fill(Side.BUY, 1.0, 50.0, idx=0),
        _cls(None, action="open", entry_price=50.0, exit_price=None),
    )
    perf.record_fill(_fill(Side.SELL, 1.0, 51.0, idx=1), _cls(10.0))
    perf.record_fill(_fill(Side.SELL, 1.0, 52.0, idx=2), _cls(-4.0))
    perf.record_fill(_fill(Side.BUY, 1.0, 53.0, idx=3), _cls(0.0))

    gross_win, gross_loss = perf.gross_pnls()
    assert gross_win == pytest.approx(10.0)
    assert gross_loss == pytest.approx(4.0)
    assert perf.profit_factor() == pytest.approx(2.5)

    # Breakevens (pnl==0) count toward win-rate denominator only.
    assert perf.win_rate() == pytest.approx(100.0 / 3.0)


def test_profit_factor_none_when_no_losing_closes_but_wins_exist() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_fill(_fill(Side.SELL, 1.0, 52.0), _cls(3.5))
    assert perf.profit_factor() is None
    gw, gl = perf.gross_pnls()
    assert gw == pytest.approx(3.5)
    assert gl == pytest.approx(0.0)


def test_realized_window_not_evicted_by_opens() -> None:
    """Opens share the fill tape but must not push realized PnL rows out."""
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio, history_size=5)

    # One close, then many opens — close must remain in KPI aggregates.
    perf.record_fill(_fill(Side.SELL, 1.0, 52.0, idx=0), _cls(2.0, action="close"))
    for i in range(1, 20):
        perf.record_fill(
            _fill(Side.BUY, 0.1, 50.0, idx=i),
            _cls(None, action="open"),
        )

    assert perf.win_rate() == pytest.approx(100.0)
    gw, gl = perf.gross_pnls()
    assert gw == pytest.approx(2.0)
    assert gl == pytest.approx(0.0)
    assert len(perf.realized_transactions()) == 1


def test_flatten_excluded_from_streak_still_counts_for_kpi() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_fill(_fill(Side.SELL, 1.0, 48.0, idx=0), _cls(-2.0), exclude_from_streak=True)

    assert perf.win_rate() == pytest.approx(0.0)
    gw, gl = perf.gross_pnls()
    assert gw == pytest.approx(0.0)
    assert gl == pytest.approx(2.0)
    assert len(perf.realized_transactions()) == 1
    row = perf.realized_transactions()[0]
    assert row.exclude_from_streak is True
    assert row.pnl == pytest.approx(-2.0)


def test_parent_close_slices_roll_up_for_rolling_not_session() -> None:
    """Rolling win rate rolls VWAP slices; session counts every reducing fill."""

    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)
    parent_id = "P-exit-1"
    slices = (-0.0524, -0.0529, -0.0527)
    for i, pnl in enumerate(slices):
        perf.record_fill(
            _fill(Side.SELL, 860.0, 0.005818, idx=i, parent_id=parent_id),
            _cls(pnl, entry_price=0.005879, exit_price=0.005818),
        )

    assert len(perf.realized_transactions()) == 0
    assert perf.session_losses == 3
    assert len(perf.trades()) == 3

    perf.finalize_parent_close(parent_id)
    assert len(perf.realized_transactions()) == 1
    assert perf.session_losses == 3
    assert perf.rolling_close_losses == 1
    assert perf.rolling_close_wins == 0
    row = perf.realized_transactions()[0]
    assert row.id == parent_id
    assert row.pnl == pytest.approx(sum(slices))
    assert perf.win_rate() == pytest.approx(0.0)
    assert perf.win_rate_session() == pytest.approx(0.0)

    # Idempotent finalize (parent done + tracker complete both call this).
    perf.finalize_parent_close(parent_id)
    assert len(perf.realized_transactions()) == 1
    assert perf.session_losses == 3


def test_session_rollups_survive_rolling_trim() -> None:
    """Session counters keep every realized close even when rolling ring evicts."""

    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio, history_size=2)
    perf.record_fill(_fill(Side.SELL, 1.0, 52.0, idx=0), _cls(1.0))
    perf.record_fill(_fill(Side.SELL, 1.0, 53.0, idx=1), _cls(2.0))
    perf.record_fill(_fill(Side.SELL, 1.0, 54.0, idx=2), _cls(-1.0))
    assert perf.session_wins == 2
    assert perf.session_losses == 1
    assert perf.session_breakevens == 0
    assert perf.win_rate_session() == pytest.approx(200.0 / 3.0)
    gw, gl = perf.gross_pnls_session()
    assert gw == pytest.approx(3.0)
    assert gl == pytest.approx(1.0)
    assert len(perf.realized_transactions()) == 2


def test_reset_session_clears_session_rollups_and_trade_tape() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_fill(_fill(Side.SELL, 1.0, 52.0, idx=0), _cls(1.0))
    perf.record_fill(_fill(Side.SELL, 1.0, 53.0, idx=1), _cls(-2.0))
    assert perf.session_wins == 1
    assert perf.session_losses == 1
    assert len(perf.trades()) == 2
    assert perf.realized_pnl_by_strategy()

    perf.reset_session()

    assert perf.session_wins == 0
    assert perf.session_losses == 0
    assert perf.win_rate_session() == pytest.approx(0.0)
    assert perf.trades() == []
    assert perf.realized_transactions() == []
    assert perf.realized_pnl_by_strategy() == {}


def test_session_fees_accumulate_on_every_fill() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    fill_open = _fill(Side.BUY, 1.0, 50.0, idx=0)
    fill_open.fee = 0.12
    fill_open.fee_asset = "USDT"
    perf.record_fill(fill_open, _cls(None, action="open"))

    fill_close = _fill(Side.SELL, 1.0, 51.0, idx=1)
    fill_close.fee = 0.08
    fill_close.fee_asset = "USDT"
    perf.record_fill(fill_close, _cls(1.0))

    assert perf.session_fees_paid == pytest.approx(0.20)


def test_session_funding_net_from_account_update() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_funding_balance_change("USDT", -0.50)
    assert perf.session_funding_net == pytest.approx(0.50)
    perf.record_funding_balance_change("USDT", 0.20)
    assert perf.session_funding_net == pytest.approx(0.30)


def test_reset_session_clears_fees_and_funding() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)
    perf.record_fill(_fill(Side.BUY, 1.0, 50.0), _cls(None, action="open"))
    perf._session_fees_usd = 1.0  # noqa: SLF001
    perf.record_funding_balance_change("USDT", -0.25)
    perf.reset_session()
    assert perf.session_fees_paid == 0.0
    assert perf.session_funding_net == 0.0


def test_reset_session_clears_session_rollups_and_trade_tape() -> None:
    portfolio = MagicMock()
    perf = PerformanceTracker(portfolio)

    perf.record_fill(_fill(Side.SELL, 1.0, 52.0, idx=0), _cls(1.0))
    perf.record_fill(_fill(Side.SELL, 1.0, 53.0, idx=1), _cls(-2.0))
    assert perf.session_wins == 1
    assert perf.session_losses == 1
    assert len(perf.trades()) == 2
    assert perf.realized_pnl_by_strategy()

    perf.reset_session()

    assert perf.session_wins == 0
    assert perf.session_losses == 0
    assert perf.win_rate_session() == pytest.approx(0.0)
    assert perf.trades() == []
    assert perf.realized_transactions() == []
    assert perf.realized_pnl_by_strategy() == {}
