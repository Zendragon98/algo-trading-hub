"""RiskManager pre-trade gate + drawdown kill switch."""

from __future__ import annotations

import os

import pytest

# We need a Settings instance for the RiskManager. Set env vars *before*
# importing the config so the singleton picks them up.
os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")
os.environ.setdefault("MAX_RISK_PCT", "0.10")
os.environ.setdefault("MAX_GROSS_NOTIONAL", "1000")
os.environ.setdefault("MAX_DRAWDOWN_PCT", "0.05")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import Signal, Tick  # noqa: E402
from engine.portfolio.portfolio import Portfolio  # noqa: E402
from engine.position.position_tracker import PositionTracker  # noqa: E402
from engine.risk.pnl_tracker import PnLTracker  # noqa: E402
from engine.risk.risk_manager import RiskManager  # noqa: E402
from engine.risk.stop_loss import StopLossMonitor  # noqa: E402
from engine.risk.limits import Limits  # noqa: E402


def _build(settings: Settings) -> tuple[RiskManager, Portfolio]:
    bus = EventBus()
    tracker = PositionTracker(bus)
    portfolio = Portfolio(bus, tracker)
    portfolio.seed_cash(1000.0)
    pnl = PnLTracker(portfolio)
    monitor = StopLossMonitor(Limits.from_settings(settings))
    return RiskManager(settings, portfolio, pnl, monitor), portfolio


def _settings() -> Settings:
    return Settings(
        binance_api_key="x", binance_api_secret="y",
        max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
        symbols=["BTCUSDT"],
    )


def test_caps_qty_to_max_risk() -> None:
    settings = _settings()
    rm, _ = _build(settings)
    # Equity=1000, max risk 10% -> 100 USDT. At mid=100 that's qty=1.
    decision = rm.check(Signal(symbol="BTCUSDT", side=Side.BUY, qty=10.0, reason="test"), mid_price=100.0)
    assert decision.approved
    assert pytest.approx(decision.qty) == 1.0


def test_rejects_when_kill_switch() -> None:
    settings = _settings()
    rm, _ = _build(settings)
    rm._kill_switch = True  # type: ignore[attr-defined]
    decision = rm.check(Signal(symbol="BTCUSDT", side=Side.BUY, qty=0.01, reason="test"), mid_price=100.0)
    assert not decision.approved


def test_monitor_no_position_returns_none() -> None:
    settings = _settings()
    rm, _ = _build(settings)
    intent = rm.monitor_tick(Tick(symbol="BTCUSDT", bid=99.5, ask=100.5), positions=[])
    assert intent is None
