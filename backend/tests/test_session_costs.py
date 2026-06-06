"""Tests for WS-derived session cost helpers."""

from __future__ import annotations

from engine.performance.session_costs import commission_to_usd, stable_usd_amount


def test_commission_to_usd_stablecoins() -> None:
    assert commission_to_usd(0.15, "USDT") == 0.15
    assert commission_to_usd(0.15, "usdc") == 0.15
    assert commission_to_usd(0.001, "BNB") == 0.0


def test_stable_usd_amount_rejects_non_stable() -> None:
    assert stable_usd_amount(1.0, "BNB") == 0.0
