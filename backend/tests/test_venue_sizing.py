from __future__ import annotations

from engine.risk.venue_sizing import venue_cap_qty, venue_qty_in_bounds
from gateways.gateway_interface import SymbolFilters


def test_venue_cap_qty_floors_to_step_at_max() -> None:
    filt = SymbolFilters(symbol="1000BONKUSDC", step_size=1.0, max_qty=100_000.0)
    assert venue_cap_qty(146_046.0, filt) == 100_000.0


def test_venue_qty_in_bounds_rejects_over_max() -> None:
    filt = SymbolFilters(symbol="X", max_qty=100.0, min_qty=1.0)
    assert venue_qty_in_bounds(50.0, filt, 1.0) is True
    assert venue_qty_in_bounds(150.0, filt, 1.0) is False
