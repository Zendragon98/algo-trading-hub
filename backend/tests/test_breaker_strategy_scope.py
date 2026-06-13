"""MM-only breakers must not block alpha strategies on the same symbol."""

from __future__ import annotations

from engine.execution.submit_guard import SubmitGuard
from engine.risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)


def _trip_toxic(breaker: CircuitBreaker, symbol: str = "BTCUSDT") -> None:
    breaker.trip(
        Breach(
            code="toxic_flow",
            scope=BreakerScope.SYMBOL,
            severity=BreakerSeverity.MINOR,
            target=symbol,
            strategy_name="market_making_v2",
        )
    )


def test_repeat_reject_blocks_only_attributed_strategy() -> None:
    breaker = CircuitBreaker()
    breaker.trip(
        Breach(
            code="repeat_reject",
            scope=BreakerScope.SYMBOL,
            severity=BreakerSeverity.MINOR,
            target="BTCUSDT",
            strategy_name="flow_momentum",
        )
    )
    assert breaker.is_blocked(
        BreakerScope.SYMBOL, "BTCUSDT", strategy_name="flow_momentum"
    )
    assert not breaker.is_blocked(
        BreakerScope.SYMBOL, "BTCUSDT", strategy_name="market_making_v2"
    )


def test_toxic_flow_blocks_mm_not_flow_momentum() -> None:
    breaker = CircuitBreaker()
    _trip_toxic(breaker)
    assert breaker.is_blocked(BreakerScope.SYMBOL, "BTCUSDT")
    assert breaker.is_blocked(
        BreakerScope.SYMBOL, "BTCUSDT", strategy_name="market_making_v2"
    )
    assert not breaker.is_blocked(
        BreakerScope.SYMBOL, "BTCUSDT", strategy_name="flow_momentum"
    )


def test_submit_guard_passes_flow_momentum_through_toxic_flow() -> None:
    breaker = CircuitBreaker()
    _trip_toxic(breaker)
    guard = SubmitGuard(
        breaker=breaker,
        open_parent_count=lambda: 0,
        max_open_parents=8,
        submit_rate_per_sec=1000.0,
        max_consecutive_rejects=3,
        reject_cooldown_sec=0.05,
    )
    ok_mm, reason_mm = guard.can_submit_parent(
        "BTCUSDT", strategy_name="market_making_v2"
    )
    ok_flow, _ = guard.can_submit_parent(
        "BTCUSDT", strategy_name="flow_momentum"
    )
    assert not ok_mm
    assert reason_mm == "symbol_breaker"
    assert ok_flow


def test_group_unwind_failed_blocks_only_pairs_strategy() -> None:
    breaker = CircuitBreaker()
    breaker.trip(
        Breach(
            code="group_unwind_failed",
            scope=BreakerScope.SYMBOL,
            severity=BreakerSeverity.MAJOR,
            target="BTCUSDT",
        )
    )
    assert breaker.is_blocked(
        BreakerScope.SYMBOL,
        "BTCUSDT",
        strategy_name="pairs_trading_usdt_usdc",
    )
    assert not breaker.is_blocked(
        BreakerScope.SYMBOL,
        "BTCUSDT",
        strategy_name="flow_momentum",
    )
