"""Per-asset wallet merge guarantees on Portfolio.

Binance ``ACCOUNT_UPDATE`` only ships the assets that *changed* in each
event, so the engine must merge the per-asset wallet map rather than
overwrite a single ``cash`` number. These tests pin that contract on
``Portfolio`` directly so a regression shows up before the engine runs.
"""

from __future__ import annotations

from common.events import EventBus
from engine.portfolio.portfolio import Portfolio
from engine.position.position_tracker import PositionTracker


def _portfolio(base_currency: str = "USDT") -> Portfolio:
    bus = EventBus()
    return Portfolio(
        bus=bus,
        position_tracker=PositionTracker(bus),
        base_currency=base_currency,
    )


def test_seed_balances_sets_cash_to_combined_stable_total() -> None:
    p = _portfolio()
    p.seed_balances({"USDT": 1000.0, "USDC": 250.0})
    # USDT base => USDT + USDC sum
    assert p.cash == 1250.0
    assert p.cash_by_asset() == {"USDT": 1000.0, "USDC": 250.0}


def test_update_asset_balance_does_not_overwrite_other_assets() -> None:
    """The ACCOUNT_UPDATE merge bug: a USDC-only event must not zero USDT."""
    p = _portfolio()
    p.seed_balances({"USDT": 1000.0, "USDC": 250.0})
    # Simulate Binance shipping only the USDC delta.
    p.update_asset_balance("USDC", 240.0)
    assert p.cash_by_asset() == {"USDT": 1000.0, "USDC": 240.0}
    assert p.cash == 1240.0


def test_update_balances_merges_partial_payload() -> None:
    """Bulk REST resync must leave unreported assets untouched."""
    p = _portfolio()
    p.seed_balances({"USDT": 500.0, "USDC": 100.0, "BNB": 5.0})
    # REST returns only USDT this round.
    p.update_balances({"USDT": 510.0})
    assert p.cash_by_asset() == {"USDT": 510.0, "USDC": 100.0, "BNB": 5.0}
    # USDT-base cash sums USDT + USDC; BNB doesn't contribute.
    assert p.cash == 610.0


def test_seed_cash_compat_shim_initialises_base_currency() -> None:
    """Legacy ``seed_cash`` should populate the base currency wallet."""
    p = _portfolio(base_currency="USDC")
    p.seed_cash(750.0)
    assert p.cash_by_asset() == {"USDC": 750.0}
    # USDC base treats USDT+USDC stable rule too.
    assert p.cash == 750.0


def test_non_stable_base_currency_returns_only_that_asset() -> None:
    """A BNB-base portfolio shouldn't sum stables into cash."""
    p = _portfolio(base_currency="BNB")
    p.seed_balances({"BNB": 12.0, "USDT": 1000.0})
    assert p.cash == 12.0
