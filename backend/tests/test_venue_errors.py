"""Venue throttle error classification."""

from __future__ import annotations

import time

from common.venue_errors import is_venue_throttle_error, venue_throttle_sleep_sec
from gateways.binance.rest_client import BinanceRestError


def test_is_venue_throttle_minus_1003() -> None:
    err = BinanceRestError(418, -1003, "REST paused 120s")
    assert is_venue_throttle_error(err)


def test_is_venue_throttle_retry_after() -> None:
    err = BinanceRestError(429, None, "Too Many Requests", retry_after_sec=30.0)
    assert is_venue_throttle_error(err)


def test_is_not_venue_throttle_trading_reject() -> None:
    err = BinanceRestError(400, -2019, "Margin is insufficient")
    assert not is_venue_throttle_error(err)


def test_venue_throttle_sleep_from_ban_message() -> None:
    future_ms = int(time.time() * 1000) + 90_000
    err = BinanceRestError(418, -1003, f"banned until {future_ms}")
    sleep = venue_throttle_sleep_sec(err)
    assert sleep is not None
    assert 80.0 < sleep < 100.0
