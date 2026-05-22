"""AUTO universe bootstrap helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from common.config import Settings
from common.universe_bootstrap import (
    _filter_and_cap_usdt_perps,
    discover_capped_usdt_perps,
    is_auto_symbol_list,
    needs_auto_universe_resolve,
    resolve_binance_auto_universe,
)


def test_is_auto_symbol_list() -> None:
    assert is_auto_symbol_list([])
    assert is_auto_symbol_list(["AUTO"])
    assert is_auto_symbol_list([" auto "])
    assert not is_auto_symbol_list(["BTCUSDT"])
    assert not is_auto_symbol_list(["BTCUSDT", "ETHUSDT"])


def test_needs_auto_universe_resolve() -> None:
    s = Settings(venue="binance", blend_symbols=["AUTO"])
    assert needs_auto_universe_resolve(s)
    s2 = Settings(
        venue="binance",
        symbols=["BTCUSDT", "BTCUSDC"],
        sma_symbols=["BTCUSDT"],
        blend_symbols=["BTCUSDT"],
        mm_symbols=["BTCUSDT"],
        mm2_symbols=["BTCUSDT"],
    )
    assert not needs_auto_universe_resolve(s2)
    s3 = Settings(venue="ibkr", blend_symbols=[])
    assert not needs_auto_universe_resolve(s3)


def test_filter_and_cap_usdt_perps() -> None:
    stats = {
        "AAAUSDT": (1000.0, 1.0),
        "BBBUSDT": (500.0, 0.001),
        "CCCUSDT": (750.0, 2.0),
    }
    universe = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    out = _filter_and_cap_usdt_perps(
        universe,
        stats,
        max_symbols=2,
        min_mid_price=0.01,
        label="TEST",
    )
    assert out == ["AAAUSDT", "CCCUSDT"]


@pytest.mark.asyncio
async def test_resolve_blend_auto() -> None:
    settings = Settings(
        venue="binance",
        blend_symbols=["AUTO"],
        blend_max_symbols=3,
        blend_min_mid_price=0.01,
        sma_symbols=["BTCUSDT"],
        symbols=["BTCUSDT", "BTCUSDC"],
        mm_symbols=["BTCUSDT"],
        mm2_symbols=["BTCUSDT"],
    )
    info = {"symbols": []}
    stats = {
        "BTCUSDT": (1e9, 50000.0),
        "ETHUSDT": (5e8, 3000.0),
        "SOLUSDT": (1e8, 100.0),
        "DOGEUSDT": (5e7, 0.1),
    }

    with (
        patch(
            "common.universe_bootstrap.discover_usdt_perps",
            return_value=list(stats.keys()),
        ),
        patch(
            "common.universe_bootstrap.BinanceRestClient",
        ) as mock_client_cls,
    ):
        mock_rest = AsyncMock()
        mock_rest.exchange_info = AsyncMock(return_value=info)
        mock_rest.fetch_24h_stats = AsyncMock(return_value=stats)
        mock_rest.close = AsyncMock()
        mock_client_cls.return_value = mock_rest

        resolved = await resolve_binance_auto_universe(settings)

    assert resolved.blend_symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert resolved.sma_symbols == ["BTCUSDT"]
    mock_rest.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_capped_usdt_perps() -> None:
    rest = AsyncMock()
    rest.fetch_24h_stats = AsyncMock(
        return_value={
            "XUSDT": (10.0, 1.0),
            "YUSDT": (20.0, 1.0),
        },
    )
    with patch(
        "common.universe_bootstrap.discover_usdt_perps",
        return_value=["XUSDT", "YUSDT"],
    ):
        out = await discover_capped_usdt_perps(
            rest,
            {},
            max_symbols=1,
            min_mid_price=0.0,
            label="TEST",
        )
    assert out == ["YUSDT"]
