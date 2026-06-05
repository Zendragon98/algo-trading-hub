"""Tests for flow entry spread and rising-tape gates."""

from __future__ import annotations

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.strategies.flow_entry_gates import entry_spread_ok, tape_rising


def _feat(spread_bps: float | None) -> Features:
    return Features(symbol="BTCUSDT", mid=100.0, spread_bps=spread_bps)


def test_entry_spread_ok_rejects_wide_spread() -> None:
    settings = Settings(flow_stop_loss_bps=14.0, flow_max_spread_entry_frac=0.4)
    assert entry_spread_ok(_feat(4.0), settings)
    assert not entry_spread_ok(_feat(8.0), settings)


def test_entry_spread_ok_respects_explicit_cap() -> None:
    settings = Settings(flow_max_spread_entry_bps=5.0, flow_stop_loss_bps=20.0)
    assert entry_spread_ok(_feat(4.0), settings)
    assert not entry_spread_ok(_feat(6.0), settings)


def test_tape_rising_long() -> None:
    assert tape_rising([0.10, 0.12, 0.15], direction=1)
    assert not tape_rising([0.15, 0.12, 0.10], direction=1)


def test_tape_rising_short() -> None:
    assert tape_rising([-0.10, -0.14, -0.18], direction=-1)
    assert not tape_rising([-0.18, -0.14, -0.10], direction=-1)
