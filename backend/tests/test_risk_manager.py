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
from common.types import Position, Signal, Tick  # noqa: E402
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


def test_per_symbol_cap_clamps_notional_before_symbol_ok() -> None:
    """When max_risk_pct exceeds max_symbol_notional_pct, size to the tighter cap.

    Otherwise we scale qty to the risk ceiling then veto every time with
    symbol_exposure_cap (production SMA logs showed this pattern).
    """
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        max_risk_pct=0.35,
        max_symbol_notional_pct=0.20,
        max_gross_notional=1_000_000.0,
        max_drawdown_pct=0.05,
        symbols=["BTCUSDT"],
    )
    rm, _ = _build(settings)
    # Equity=1000, per-symbol cap 20% -> 200 USDT max; risk cap would allow 350.
    decision = rm.check(Signal(symbol="BTCUSDT", side=Side.BUY, qty=1_000_000.0, reason="test"), mid_price=100.0)
    assert decision.approved
    assert pytest.approx(decision.qty) == 2.0  # 200 / 100


def test_monitor_tick_trips_max_drawdown() -> None:
    """Session drawdown at or above the cap latches the engine breaker."""
    settings = _settings()
    rm, portfolio = _build(settings)
    portfolio.seed_cash(1000.0)
    # 6% drawdown with a 5% cap.
    portfolio.update_cash(940.0)
    rm._pnl.update()
    tick = Tick(symbol="BTCUSDT", bid=99.5, ask=100.5)
    rm.monitor_tick(tick, positions=[])
    assert rm.kill_switch is True
    codes = {s.code for s in rm.breaker.active()}
    assert "max_drawdown" in codes


def test_rejects_when_kill_switch() -> None:
    """Engine-scope MAJOR breach blocks every entry path.

    The legacy `_kill_switch` flag is gone; `RiskManager` consults the
    shared CircuitBreaker instead, so we trip an engine-scope MAJOR and
    confirm the next pre-trade gate vetoes.
    """
    from engine.risk.circuit_breaker import (  # noqa: WPS433  -- test-local import
        Breach, BreakerScope, BreakerSeverity,
    )
    settings = _settings()
    rm, _ = _build(settings)
    rm.breaker.trip(Breach(
        code="test", scope=BreakerScope.ENGINE, severity=BreakerSeverity.MAJOR,
    ))
    decision = rm.check(Signal(symbol="BTCUSDT", side=Side.BUY, qty=0.01, reason="test"), mid_price=100.0)
    assert not decision.approved
    assert rm.kill_switch is True


def test_monitor_no_position_returns_none() -> None:
    settings = _settings()
    rm, _ = _build(settings)
    intent = rm.monitor_tick(Tick(symbol="BTCUSDT", bid=99.5, ask=100.5), positions=[])
    assert intent is None


def test_stop_loss_cooldown_suppresses_repeat_triggers() -> None:
    """One adverse tick may fire the SL; subsequent ticks within the
    cooldown must NOT keep emitting fresh exits.

    Repro for the production log spam where a single sub-MIN_NOTIONAL
    BTCUSDT position generated dozens of `stop_loss triggered` lines
    per second because each clock tick re-evaluated the bracket while
    the previous closing order was still being placed (or rejected).
    """
    monitor = StopLossMonitor(
        Limits(
            max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
            default_stop_loss_pct=0.01, default_take_profit_pct=0.02,
        ),
        cooldown_sec=60.0,
    )
    pos = Position(symbol="BTCUSDT", qty=0.0002, avg_entry_price=80_000.0)
    monitor.arm(pos)

    deep_below = Tick(symbol="BTCUSDT", bid=70_000.0, ask=70_001.0)
    assert monitor.evaluate(pos, deep_below) == "stop_loss"
    # While the closing order is still in flight (or just got rejected),
    # the next clock tick must NOT emit a duplicate exit.
    assert monitor.evaluate(pos, deep_below) is None
    assert monitor.evaluate(pos, deep_below) is None


def test_stop_loss_cooldown_is_cleared_on_rearm() -> None:
    """A position that gets re-armed (e.g. partial fill) is allowed to
    fire immediately on the next adverse tick — the cooldown belongs to
    the previous bracket, not the new one."""
    monitor = StopLossMonitor(
        Limits(
            max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
            default_stop_loss_pct=0.01, default_take_profit_pct=0.02,
        ),
        cooldown_sec=60.0,
    )
    pos = Position(symbol="BTCUSDT", qty=0.0002, avg_entry_price=80_000.0)
    monitor.arm(pos)
    assert monitor.evaluate(pos, Tick(symbol="BTCUSDT", bid=70_000.0, ask=70_001.0)) == "stop_loss"

    larger = Position(symbol="BTCUSDT", qty=0.0004, avg_entry_price=80_000.0)
    monitor.arm(larger)
    assert monitor.evaluate(larger, Tick(symbol="BTCUSDT", bid=70_000.0, ask=70_001.0)) == "stop_loss"


def test_stop_loss_cooldown_preserved_on_closing_fill_rearm() -> None:
    """A re-arm caused by a *closing* fill (position shrank, not grew)
    must NOT reset the cooldown — otherwise every partial fill of the
    in-flight reduce-only exit re-opens the SL gate and the engine
    cascades a fresh closing parent on every tick.

    This is the exact production scenario behind the runaway log spam:
        16:42:48 stop_loss triggered on ETHUSDC @ 2318.13
        16:42:48 armed bracket ETHUSDC entry=2306.37 stop=2317.91
        16:42:49 stop_loss triggered on ETHUSDC @ 2318.13
        16:42:50 stop_loss triggered on ETHUSDC @ 2318.13
        ... (one new closing VWAP per second)
    """
    monitor = StopLossMonitor(
        Limits(
            max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
            default_stop_loss_pct=0.005, default_take_profit_pct=0.01,
        ),
        cooldown_sec=60.0,
    )
    # Open short at 2306; SL sits at 2317.91 (entry * 1.005).
    pos = Position(symbol="ETHUSDC", qty=-0.72, avg_entry_price=2306.37)
    monitor.arm(pos)
    above_stop = Tick(symbol="ETHUSDC", bid=2318.10, ask=2318.13)
    assert monitor.evaluate(pos, above_stop) == "stop_loss"

    # Reduce-only closing fill arrives; position shrinks but isn't flat
    # yet. PositionTracker keeps avg_entry_price unchanged on partial
    # closes, so the bracket prices are identical.
    after_partial_close = Position(symbol="ETHUSDC", qty=-0.60, avg_entry_price=2306.37)
    monitor.arm(after_partial_close)

    # The next adverse tick must NOT re-trigger; the closing parent is
    # still working through the venue.
    assert monitor.evaluate(after_partial_close, above_stop) is None
    assert monitor.evaluate(after_partial_close, above_stop) is None


def test_externally_managed_symbol_skips_per_leg_bracket() -> None:
    """Symbols owned by a strategy with `manages_own_risk()` must
    bypass the StopLossMonitor entirely. The engine wires this for
    pair-traded symbols so a normal correlated tick on either leg
    doesn't trip a fixed-% bracket and unwind a healthy basis trade.
    """
    monitor = StopLossMonitor(
        Limits(
            max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
            default_stop_loss_pct=0.005, default_take_profit_pct=0.01,
        ),
        cooldown_sec=60.0,
        externally_managed={"ETHUSDC", "ETHUSDT"},
    )
    pos = Position(symbol="ETHUSDC", qty=-0.5, avg_entry_price=2306.0)
    # arm() is a no-op (returns None) instead of raising or setting a
    # bracket; subsequent evaluate() calls must always return None even
    # if the tick is far past where a fixed-% bracket would have fired.
    assert monitor.arm(pos) is None
    way_above = Tick(symbol="ETHUSDC", bid=2400.0, ask=2400.1)
    assert monitor.evaluate(pos, way_above) is None

    # Sibling symbol that is NOT excluded still gets the per-leg bracket
    # (single-leg strategies opt in by default).
    btc = Position(symbol="BTCUSDT", qty=0.001, avg_entry_price=80_000.0)
    monitor.arm(btc)
    assert monitor.evaluate(btc, Tick(symbol="BTCUSDT", bid=70_000.0, ask=70_001.0)) == "stop_loss"


def test_stop_loss_cooldown_cleared_on_flip() -> None:
    """A direction flip (e.g. SL closes long and reverses to short) is
    a brand-new bracket and must be allowed to fire immediately."""
    monitor = StopLossMonitor(
        Limits(
            max_risk_pct=0.10, max_gross_notional=1000.0, max_drawdown_pct=0.05,
            default_stop_loss_pct=0.01, default_take_profit_pct=0.02,
        ),
        cooldown_sec=60.0,
    )
    long_pos = Position(symbol="BTCUSDT", qty=0.001, avg_entry_price=80_000.0)
    monitor.arm(long_pos)
    assert monitor.evaluate(long_pos, Tick(symbol="BTCUSDT", bid=70_000.0, ask=70_001.0)) == "stop_loss"

    short_pos = Position(symbol="BTCUSDT", qty=-0.001, avg_entry_price=70_000.0)
    monitor.arm(short_pos)
    # Short stop sits above entry; an ask above the stop must fire.
    assert monitor.evaluate(short_pos, Tick(symbol="BTCUSDT", bid=80_000.0, ask=80_001.0)) == "stop_loss"
