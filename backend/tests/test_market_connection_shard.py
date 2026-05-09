"""Shard sizing for Binance combined-stream 1024-stream cap."""

from __future__ import annotations

from gateways.binance.market_connection import _MAX_STREAMS_PER_CONNECTION, _shard_symbols_for_streams


def test_shard_counts_respect_binance_limit() -> None:
    # 528 symbols like AUTO SMA universe → must split into multiple shards
    syms = [f"S{i}USDT".lower() for i in range(528)]
    shards = _shard_symbols_for_streams(syms)
    for chunk, include_arr in shards:
        n = (1 if include_arr else 0) + len(chunk) * 3
        assert n <= _MAX_STREAMS_PER_CONNECTION
    assert len(shards) >= 2


def test_small_universe_single_shard() -> None:
    syms = ["btcusdt", "ethusdt"]
    shards = _shard_symbols_for_streams(syms)
    assert len(shards) == 1
    assert shards[0][1] is True
    assert len(shards[0][0]) == 2
