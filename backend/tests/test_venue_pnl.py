"""Tests for test_flow_momentum.py updates - venue_pnl module."""

from __future__ import annotations

import logging

import pytest

from engine.position.venue_pnl import compute_venue_pnl, resolve_entry_price
from engine.strategies.position_sync import VenuePosition

pytestmark = pytest.mark.filterwarnings("ignore")


def test_entry_hierarchy_fill_vwap_beats_venue() -> None:
    venue = VenuePosition(qty=1.0, avg_entry_price=99.0, mark_price=100.0)
    entry, source = resolve_entry_price(
        venue=venue, pos_side=1, pos_qty=1.0, fill_vwap=98.0
    )
    assert source == "fill_vwap"
    assert entry == 98.0


def test_venue_upnl_requires_qty_alignment() -> None:
    venue = VenuePosition(
        qty=10.0,
        avg_entry_price=100.0,
        mark_price=101.0,
        exchange_unrealized_pnl=10.0,
    )
    snap = compute_venue_pnl(
        pos_side=1,
        pos_qty=1.0,
        mid=101.0,
        fill_vwap=0.0,
        venue=venue,
    )
    assert snap.venue_bps is None
    assert snap.qty_aligned is False
