"""CircuitBreaker state machine + scope/severity dispatch."""

from __future__ import annotations

import time

from engine.risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    BreakerState,
    CircuitBreaker,
)


def _minor(code: str, scope: BreakerScope, target: str | None = None,
           cooldown_sec: float = 0.0) -> Breach:
    return Breach(
        code=code, scope=scope, severity=BreakerSeverity.MINOR,
        target=target, cooldown_sec=cooldown_sec,
    )


def _major(code: str, scope: BreakerScope, target: str | None = None) -> Breach:
    return Breach(
        code=code, scope=scope, severity=BreakerSeverity.MAJOR, target=target,
    )


def test_minor_breach_blocks_then_auto_resumes() -> None:
    cb = CircuitBreaker()
    cb.trip(_minor("stale_tick", BreakerScope.SYMBOL, target="BTCUSDT", cooldown_sec=0.05))
    assert cb.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    # Different symbol unaffected.
    assert not cb.is_blocked(BreakerScope.SYMBOL, "ETHUSDT")
    # After the cooldown elapses, tick() must clear the breach.
    time.sleep(0.06)
    cb.tick()
    assert not cb.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    # And it should appear in history with state ARMED.
    hist = cb.history()
    assert any(s.code == "stale_tick" and s.state is BreakerState.ARMED for s in hist)


def test_major_latched_until_rearm() -> None:
    cb = CircuitBreaker()
    cb.trip(_major("max_drawdown", BreakerScope.ENGINE))
    assert cb.is_blocked(BreakerScope.ENGINE)
    cb.tick()
    # Still latched even after a tick (no cooldown clears MAJOR).
    assert cb.is_blocked(BreakerScope.ENGINE)
    cb.rearm(code="max_drawdown")
    assert not cb.is_blocked(BreakerScope.ENGINE)


def test_engine_scope_blocks_symbol_and_parent_queries() -> None:
    cb = CircuitBreaker()
    cb.trip(_major("engine_kill", BreakerScope.ENGINE))
    assert cb.is_blocked(BreakerScope.ENGINE)
    assert cb.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    assert cb.is_blocked(BreakerScope.PARENT, "P-1")


def test_symbol_scope_does_not_block_engine_query() -> None:
    cb = CircuitBreaker()
    cb.trip(_minor("repeat_reject", BreakerScope.SYMBOL, target="BTCUSDT", cooldown_sec=10))
    assert cb.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    assert not cb.is_blocked(BreakerScope.ENGINE)


def test_minor_engine_breach_does_not_halt() -> None:
    cb = CircuitBreaker()
    cb.trip(_minor("stale_user_data", BreakerScope.ENGINE, cooldown_sec=10.0))
    assert cb.is_blocked(BreakerScope.ENGINE) is False
    assert not cb.is_engine_halted()
    assert any(s.code == "stale_user_data" for s in cb.active())


def test_minor_cannot_demote_active_major() -> None:
    cb = CircuitBreaker()
    cb.trip(_major("daily_loss", BreakerScope.ENGINE))
    cb.trip(Breach(
        code="daily_loss", scope=BreakerScope.ENGINE,
        severity=BreakerSeverity.MINOR, cooldown_sec=0.01,
    ))
    time.sleep(0.02)
    cb.tick()
    # Still latched — minor re-trip refreshed timestamp but did not demote.
    assert cb.is_blocked(BreakerScope.ENGINE)


def test_rearm_filters() -> None:
    cb = CircuitBreaker()
    cb.trip(_major("max_drawdown", BreakerScope.ENGINE))
    cb.trip(_major("daily_loss", BreakerScope.ENGINE))
    # Targeted rearm clears only the matching code.
    cb.rearm(code="daily_loss")
    codes = {s.code for s in cb.active()}
    assert codes == {"max_drawdown"}
    cb.rearm()  # clear-all
    assert cb.active() == []


def test_parent_scope_isolation() -> None:
    cb = CircuitBreaker()
    cb.trip(_minor("slippage_breach", BreakerScope.PARENT, target="P-1", cooldown_sec=10))
    assert cb.is_blocked(BreakerScope.PARENT, "P-1")
    assert not cb.is_blocked(BreakerScope.PARENT, "P-2")
    assert not cb.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
