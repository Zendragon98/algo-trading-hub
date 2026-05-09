"""Binance REST rate-limit metadata on BinanceRestError."""

from __future__ import annotations

import time

import httpx

from gateways.binance.rest_client import BinanceRestError, parse_retry_after_header


def test_parse_retry_after_seconds() -> None:
    h = httpx.Headers({"Retry-After": "42"})
    assert parse_retry_after_header(h) == 42.0


def test_parse_retry_after_case_insensitive_key() -> None:
    h = httpx.Headers({"retry-after": "3"})
    assert parse_retry_after_header(h) == 3.0


def test_binance_rest_error_sets_retry_after_for_minus_1003() -> None:
    future_ms = int(time.time() * 1000) + 60_000
    msg = f"code=-1003: banned until {future_ms}"
    err = BinanceRestError(418, -1003, msg)
    assert err.retry_after_sec is not None
    assert 55.0 < err.retry_after_sec < 65.0


def test_binance_rest_error_minus_1003_without_timestamp_uses_default() -> None:
    err = BinanceRestError(429, -1003, "Way too many requests")
    assert err.retry_after_sec == 120.0


def test_binance_rest_error_explicit_retry_overrides_parsing() -> None:
    err = BinanceRestError(429, None, "Too Many Requests", retry_after_sec=15.0)
    assert err.retry_after_sec == 15.0
