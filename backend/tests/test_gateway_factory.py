"""Gateway factory selection + LIVE-mode safety guarantees."""

from __future__ import annotations

import pytest

from common.config import Settings
from common.enums import TradingMode
from engine.execution.impact_model import ImpactConfig, ImpactModel
from gateways.binance.binance_gateway import BinanceGateway
from gateways.factory import create_gateway, supported_venues
from gateways.ibkr.ibkr_gateway import IBKRGateway


def _settings(**overrides) -> Settings:
    base = dict(binance_api_key="x", binance_api_secret="y", symbols=["BTCUSDT"])
    base.update(overrides)
    return Settings(**base)


def test_supported_venues_lists_binance_and_ibkr() -> None:
    assert "binance" in supported_venues()
    assert "ibkr" in supported_venues()


def test_factory_returns_binance_gateway_by_default() -> None:
    gateway = create_gateway(_settings())
    assert isinstance(gateway, BinanceGateway)


def test_factory_is_case_insensitive() -> None:
    gateway = create_gateway(_settings(venue="Binance"))
    assert isinstance(gateway, BinanceGateway)


def test_factory_returns_ibkr_skeleton() -> None:
    gateway = create_gateway(_settings(venue="ibkr"))
    assert isinstance(gateway, IBKRGateway)


def test_factory_rejects_unknown_venue() -> None:
    with pytest.raises(ValueError) as exc:
        create_gateway(_settings(venue="kraken"))
    msg = str(exc.value)
    assert "kraken" in msg
    assert "binance" in msg


def test_impact_config_from_settings_is_always_disabled() -> None:
    """Production wiring does not enable the optional square-root model."""
    live = _settings(trading_mode=TradingMode.LIVE)
    paper = _settings(trading_mode=TradingMode.PAPER)
    assert ImpactConfig.from_settings(live).enabled is False
    assert ImpactConfig.from_settings(paper).enabled is False


def test_impact_model_explicit_enabled_can_adjust_price() -> None:
    from common.enums import Side
    from engine.market_data.orderbook import OrderBook

    book = OrderBook(symbol="BTCUSDT")
    book.apply_snapshot(
        bids=[(100.0, 100.0)] * 3,
        asks=[(100.5, 100.0)] * 3,
        last_update_id=1,
    )
    model = ImpactModel(ImpactConfig(enabled=True, k=0.5, top_n=3))

    price, bps = model.apply(Side.BUY, qty=50.0, raw_price=100.5, book=book)
    assert bps > 0.0
    assert price > 100.5


@pytest.mark.asyncio
async def test_ibkr_skeleton_methods_signal_unimplemented() -> None:
    """The skeleton must conform to the interface but advertise it isn't done."""
    gateway = IBKRGateway(_settings(venue="ibkr"))
    with pytest.raises(NotImplementedError):
        await gateway.connect()
    with pytest.raises(NotImplementedError):
        await gateway.fetch_balance()
    with pytest.raises(NotImplementedError):
        await gateway.book_snapshot("BTCUSDT")
