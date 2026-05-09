"""Tests for on-disk Binance leverage bracket cache."""

import json
import time
from types import SimpleNamespace

from gateways.binance.leverage_bracket_cache import (
    load_leverage_bracket_cache,
    save_leverage_bracket_cache,
    leverage_bracket_cache_path,
)


def test_cache_round_trip(tmp_path) -> None:
    path = tmp_path / "cache.json"
    rest = "https://testnet.binancefuture.com"
    caps_in = {"BTCUSDT": 125, "ETHUSDT": 100}
    save_leverage_bracket_cache(path, rest, caps_in)
    out = load_leverage_bracket_cache(path, rest, ttl_sec=0)
    assert out == caps_in


def test_cache_respects_ttl(tmp_path) -> None:
    path = tmp_path / "cache.json"
    rest = "https://testnet.binancefuture.com"
    old = time.time() - 4000.0
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rest_base": rest,
                "fetched_at": old,
                "caps": {"BTCUSDT": 50},
            },
        ),
        encoding="utf-8",
    )
    assert load_leverage_bracket_cache(path, rest, ttl_sec=3600) is None
    assert load_leverage_bracket_cache(path, rest, ttl_sec=0) == {"BTCUSDT": 50}


def test_cache_rest_base_mismatch(tmp_path) -> None:
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rest_base": "https://fapi.binance.com",
                "fetched_at": time.time(),
                "caps": {"BTCUSDT": 125},
            },
        ),
        encoding="utf-8",
    )
    assert load_leverage_bracket_cache(path, "https://testnet.binancefuture.com", 0) is None


def test_ignore_ttl_loads_expired(tmp_path) -> None:
    path = tmp_path / "cache.json"
    rest = "https://testnet.binancefuture.com"
    old = time.time() - 100_000.0
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rest_base": rest,
                "fetched_at": old,
                "caps": {"XRPUSDT": 20},
            },
        ),
        encoding="utf-8",
    )
    assert load_leverage_bracket_cache(path, rest, ttl_sec=3600) is None
    got = load_leverage_bracket_cache(
        path, rest, ttl_sec=3600, ignore_ttl=True,
    )
    assert got == {"XRPUSDT": 20}


def test_leverage_bracket_cache_path_relative() -> None:
    p = leverage_bracket_cache_path(
        SimpleNamespace(leverage_bracket_cache_path="data/cache/x.json"),
    )
    assert p.name == "x.json"
    assert p.parent.name == "cache"


def test_save_skips_empty_caps(tmp_path) -> None:
    path = tmp_path / "cache.json"
    save_leverage_bracket_cache(path, "https://x.com", {})
    assert not path.exists()
