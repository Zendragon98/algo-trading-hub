"""Per-strategy fill VWAP ledger and attributed PnL isolation."""

from __future__ import annotations

import pytest

from common.enums import Side
from engine.position.strategy_ledger import StrategyPositionLedger
from engine.position.venue_pnl import compute_venue_pnl, inventory_pnl_bps
from engine.strategies.position_sync import VenuePosition


def test_ledger_fill_vwap_accumulates_on_scale_in() -> None:
    ledger = StrategyPositionLedger()
    ledger.apply_fill("mm", "ETHUSDT", Side.BUY, 1.0, price=100.0)
    ledger.apply_fill("mm", "ETHUSDT", Side.BUY, 1.0, price=102.0)
    assert ledger.qty("mm", "ETHUSDT") == pytest.approx(2.0)
    assert ledger.fill_vwap("mm", "ETHUSDT") == pytest.approx(101.0)


def test_ledger_fill_vwap_kept_on_partial_close() -> None:
    ledger = StrategyPositionLedger()
    ledger.apply_fill("flow", "BTCUSDT", Side.BUY, 2.0, price=100.0)
    ledger.apply_fill("flow", "BTCUSDT", Side.SELL, 0.5, price=105.0)
    assert ledger.qty("flow", "BTCUSDT") == pytest.approx(1.5)
    assert ledger.fill_vwap("flow", "BTCUSDT") == pytest.approx(100.0)


def test_ledger_clears_on_flat() -> None:
    ledger = StrategyPositionLedger()
    ledger.apply_fill("flow", "BTCUSDT", Side.BUY, 1.0, price=100.0)
    ledger.apply_fill("flow", "BTCUSDT", Side.SELL, 1.0, price=101.0)
    assert ledger.qty("flow", "BTCUSDT") == pytest.approx(0.0)
    assert ledger.fill_vwap("flow", "BTCUSDT") == pytest.approx(0.0)


def test_attributed_pnl_ignores_net_venue_when_qty_mismatch() -> None:
    """Flow owns 1 ETH; venue net is 3 ETH — flow must not inherit venue up."""
    venue = VenuePosition(
        qty=3.0,
        avg_entry_price=100.0,
        mark_price=101.0,
        exchange_unrealized_pnl=3.0,
    )
    snap = compute_venue_pnl(
        pos_side=1,
        pos_qty=1.0,
        mid=101.0,
        fill_vwap=100.5,
        venue=venue,
    )
    assert snap.qty_aligned is False
    assert snap.venue_bps is None
    assert snap.entry_source == "fill_vwap"
    assert snap.exit_bps == pytest.approx(49.75, rel=1e-3)


def test_mm_inventory_pnl_uses_attributed_leg() -> None:
    venue = VenuePosition(
        qty=2.0,
        avg_entry_price=100.0,
        mark_price=100.0,
        exchange_unrealized_pnl=0.0,
    )
    pnl_bps, snap = inventory_pnl_bps(
        fill_entry=99.0,
        book_mid=100.0,
        position_qty=1.0,
        venue=venue,
    )
    assert snap is not None
    assert snap.qty_aligned is False
    assert pnl_bps == pytest.approx(101.01, rel=1e-3)
