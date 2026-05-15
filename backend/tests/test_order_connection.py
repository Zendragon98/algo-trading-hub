"""Binance order connection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from gateways.binance.order_connection import OrderConnection


def test_user_ws_url_appends_private_route() -> None:
    rest = MagicMock()
    conn = OrderConnection(rest, "wss://stream.binancefuture.com")
    conn._listen_key = "abc123"
    assert conn._user_ws_url() == "wss://stream.binancefuture.com/private/ws/abc123"


def test_user_ws_url_when_base_already_private() -> None:
    rest = MagicMock()
    conn = OrderConnection(rest, "wss://fstream.binance.com/private")
    conn._listen_key = "xyz"
    assert conn._user_ws_url() == "wss://fstream.binance.com/private/ws/xyz"
