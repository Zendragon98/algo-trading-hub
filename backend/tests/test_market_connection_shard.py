"""Shard sizing for Binance combined-stream 1024-stream cap."""

from __future__ import annotations

from gateways.binance.market_connection import (
    _MAX_STREAMS_PER_CONNECTION,
    _MAX_STREAM_URL_CHARS,
    _joined_stream_url_len,
    _shard_symbols_for_streams,
    _stream_parts_for_symbols,
)


def test_shard_counts_respect_binance_limit() -> None:
    # 528 symbols like AUTO SMA universe → must split into multiple shards
    syms = [f"S{i}USDT".lower() for i in range(528)]
    shards = _shard_symbols_for_streams(syms)
    for chunk, include_arr in shards:
        n = (1 if include_arr else 0) + len(chunk) * 3
        assert n <= _MAX_STREAMS_PER_CONNECTION
    assert len(shards) >= 2


def test_small_universe_ticker_isolated() -> None:
    syms = ["btcusdt", "ethusdt"]
    shards = _shard_symbols_for_streams(syms)
    assert len(shards) == 2
    assert shards[0] == ([], True)
    assert shards[1][1] is False
    assert len(shards[1][0]) == 2


def test_shard_respects_url_length() -> None:
    # Long symbol names blow past proxy URL limits before the 1024-stream cap.
    syms = [f"1000LONGNAME{i}USDT".lower() for i in range(60)]
    shards = _shard_symbols_for_streams(syms)
    assert len(shards) >= 3
    for chunk, include_arr in shards:
        parts = (["!ticker@arr"] if include_arr else []) + _stream_parts_for_symbols(chunk)
        assert _joined_stream_url_len(parts) <= _MAX_STREAM_URL_CHARS
        n = len(parts)
        assert n <= _MAX_STREAMS_PER_CONNECTION


def test_eighty_eight_symbol_universe_splits_across_data_shards() -> None:
    syms = [f"S{i:02d}USDT".lower() for i in range(88)]
    shards = _shard_symbols_for_streams(syms)
    data_shards = [c for c, inc in shards if c]
    assert len(data_shards) >= 2
    assert sum(len(c) for c in data_shards) == 88
