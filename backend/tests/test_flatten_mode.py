from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.types import Position  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402


class _EngineStub:
    """Minimal surface to exercise flatten mode selection."""

    def __init__(self, settings: Settings, feats: dict[str, Features]) -> None:
        self._settings = settings
        self._features = type("_F", (), {"snapshot": lambda _s, sym: feats.get(sym, Features(symbol=sym))})()
        self._latest_tick = {}
        self._books = {}

    def _mid_for(self, symbol: str) -> float | None:
        f = self._features.snapshot(symbol)
        return f.mid


def _mode(engine: _EngineStub, pos: Position, *, retry: bool = False) -> str:
    from engine.core.engine import Engine

    return Engine._flatten_close_mode(engine, pos, retry=retry)  # noqa: SLF001


def test_flatten_retry_uses_market() -> None:
    s = Settings(binance_api_key="x", binance_api_secret="y")
    eng = _EngineStub(s, {"BTCUSDT": Features(symbol="BTCUSDT", mid=100_000.0)})
    pos = Position(symbol="BTCUSDT", qty=1.0)
    assert _mode(eng, pos, retry=True) == "market"


def test_flatten_small_notional_uses_market() -> None:
    s = Settings(binance_api_key="x", binance_api_secret="y", flatten_market_max_notional_usd=500.0)
    eng = _EngineStub(s, {"DOGEUSDT": Features(symbol="DOGEUSDT", mid=0.1, spread_bps=5.0)})
    pos = Position(symbol="DOGEUSDT", qty=100.0)  # $10 notional
    assert _mode(eng, pos) == "market"


def test_flatten_large_tight_spread_uses_passive_vwap() -> None:
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        flatten_vwap_min_notional_usd=1000.0,
        flatten_passive_spread_bps=25.0,
    )
    eng = _EngineStub(s, {"ETHUSDT": Features(symbol="ETHUSDT", mid=3000.0, spread_bps=10.0)})
    pos = Position(symbol="ETHUSDT", qty=2.0)  # $6000
    assert _mode(eng, pos) == "flatten_passive"


def test_flatten_medium_uses_aggressive_vwap() -> None:
    s = Settings(binance_api_key="x", binance_api_secret="y", flatten_vwap_min_notional_usd=5000.0)
    eng = _EngineStub(s, {"SOLUSDT": Features(symbol="SOLUSDT", mid=150.0, spread_bps=40.0)})
    pos = Position(symbol="SOLUSDT", qty=10.0)  # $1500, wide vs passive cap
    assert _mode(eng, pos) == "flatten"
