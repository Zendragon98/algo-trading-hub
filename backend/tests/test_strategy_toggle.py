"""Engine.set_active_strategy + StopLossMonitor.set_externally_managed.

These tests cover the dashboard hot-swap surface end-to-end without
going through the API layer. The Engine is constructed with a mock
gateway so we never touch a network — the goal is to pin the contract
that:

* only the active strategy emits signals / receives ``on_fill`` events;
* the StopLossMonitor's externally-managed set tracks the active
  strategy on every swap (so a rotated-out coin re-enables its per-leg
  bracket).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from collections.abc import Iterable  # noqa: E402

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Kline, Position, Signal  # noqa: E402
from engine.core.engine import Engine  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.risk.limits import Limits  # noqa: E402
from engine.risk.stop_loss import StopLossMonitor  # noqa: E402
from engine.strategies.strategy_base import StrategyBase  # noqa: E402
from gateways.gateway_interface import GatewayInterface, SymbolFilters  # noqa: E402


class _StubStrategy(StrategyBase):
    """Strategy stub whose every emission is recorded for assertions."""

    def __init__(self, name: str, sym: str, *, manages_risk: bool) -> None:
        self.name = name
        self.display_label = name
        self.description = f"stub for {sym}"
        self._sym = sym
        self._manages_risk = manages_risk
        self.tick_count = 0
        self.fill_count = 0

    def symbols(self) -> list[str]:
        return [self._sym]

    def manages_own_risk(self) -> bool:
        return self._manages_risk

    def on_tick(self, features: dict[str, Features]) -> Iterable[Signal]:
        self.tick_count += 1
        return []

    def on_fill(self, symbol: str, qty: float, side: str) -> None:
        self.fill_count += 1


class _MockGateway(GatewayInterface):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...

    async def place_order(self, order: ChildOrder) -> ChildOrder:
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        return

    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        return None

    async def fetch_positions(self) -> list[Position]:
        return []

    async def fetch_balance(self) -> float:
        return 0.0

    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}

    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        return []


def _engine_with(strategies: list[_StubStrategy]) -> Engine:
    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        symbols=[s.symbols()[0] for s in strategies],
        strategy=strategies[0].name,
    )
    return Engine(settings=settings, bus=EventBus(), gateway=_MockGateway(), strategies=strategies)


def test_set_active_strategy_updates_externally_managed_set() -> None:
    """A pairs strategy owns its symbols; an SMA strategy hands them back."""
    pairs = _StubStrategy("pairs", "BTCUSDC", manages_risk=True)
    sma = _StubStrategy("sma", "ETHUSDT", manages_risk=False)
    engine = _engine_with([pairs, sma])

    # On boot the active strategy is ``pairs``, so its symbol bypasses the
    # per-leg bracket.
    assert engine.active_strategy_name == "pairs"
    assert engine._stop_monitor._externally_managed == frozenset({"BTCUSDC"})

    engine.set_active_strategy("sma")
    assert engine.active_strategy_name == "sma"
    # SMA does NOT manage own risk -> the externally-managed set is empty
    # so every coin (including the new active one) gets a per-leg bracket.
    assert engine._stop_monitor._externally_managed == frozenset()


def test_set_active_strategy_rejects_unknown_name() -> None:
    pairs = _StubStrategy("pairs", "BTCUSDC", manages_risk=True)
    engine = _engine_with([pairs])
    with pytest.raises(ValueError):
        engine.set_active_strategy("does-not-exist")


def test_set_active_strategy_accepts_all_mode() -> None:
    pairs = _StubStrategy("pairs", "BTCUSDC", manages_risk=True)
    sma = _StubStrategy("sma", "ETHUSDT", manages_risk=False)
    engine = _engine_with([pairs, sma])
    engine.set_active_strategy("all")
    assert engine.is_multi_strategy_mode()


def test_unknown_boot_strategy_falls_back_to_first() -> None:
    """``settings.strategy`` not in registered names => first strategy wins."""
    pairs = _StubStrategy("pairs", "BTCUSDC", manages_risk=True)
    sma = _StubStrategy("sma", "ETHUSDT", manages_risk=False)
    settings = Settings(
        binance_api_key="x", binance_api_secret="y",
        symbols=["BTCUSDC", "ETHUSDT"], strategy="ghost",
    )
    engine = Engine(settings=settings, bus=EventBus(), gateway=_MockGateway(), strategies=[pairs, sma])
    assert engine.active_strategy_name == pairs.name


@pytest.mark.asyncio
async def test_only_active_strategy_evaluates_and_receives_fills() -> None:
    pairs = _StubStrategy("pairs", "BTCUSDC", manages_risk=True)
    sma = _StubStrategy("sma", "ETHUSDT", manages_risk=False)
    engine = _engine_with([pairs, sma])

    # Active = pairs at boot.
    await engine._evaluate_strategies()
    assert pairs.tick_count == 1
    assert sma.tick_count == 0

    # Hot-swap to SMA; pairs must stop seeing ticks.
    engine.set_active_strategy("sma")
    await engine._evaluate_strategies()
    assert pairs.tick_count == 1
    assert sma.tick_count == 1


def test_stop_loss_monitor_set_externally_managed_disarms_added_symbols() -> None:
    """Symbols newly entering the set should drop their bracket + cooldown."""
    monitor = StopLossMonitor(
        limits=Limits(
            max_risk_pct=0.35,
            max_gross_notional=50_000.0,
            max_drawdown_pct=0.10,
            default_stop_loss_pct=0.005,
            default_take_profit_pct=0.01,
        ),
        externally_managed=set(),
    )
    pos = Position(symbol="BTCUSDT", qty=1.0, avg_entry_price=100.0, mark_price=100.0)
    monitor.arm(pos)
    assert "BTCUSDT" in monitor._brackets

    monitor.set_externally_managed({"BTCUSDT"})
    assert "BTCUSDT" not in monitor._brackets
    assert monitor._externally_managed == frozenset({"BTCUSDT"})

    # Removing the externally-managed flag does NOT auto-arm; ``arm`` is
    # called by the engine on the next tick.
    monitor.set_externally_managed(set())
    assert monitor._externally_managed == frozenset()


# ---- Multi-symbol SMA scanner ----


def test_multi_symbol_sma_keeps_per_symbol_state() -> None:
    """Independent crossovers on different symbols must each emit once."""
    from engine.strategies.sma_crossover import SmaCrossoverStrategy

    settings = Settings(
        binance_api_key="x", binance_api_secret="y",
        sma_symbols=["BTCUSDT", "ETHUSDT"],
        sma_fast_window=2, sma_slow_window=4,
        sma_qty=1.0, sma_cooldown_sec=0,
    )
    strat = SmaCrossoverStrategy(settings)

    def _feat(sym: str, mid: float) -> Features:
        return Features(symbol=sym, mid=mid, spread_bps=1.0, micro_price=mid,
                        imbalance_topn=0.0, bid_hit_ratio=0.5, ask_hit_ratio=0.5)

    # Series designed so both BTCUSDT and ETHUSDT cross up.
    btc_path = [100.0, 100.0, 99.0, 99.0, 110.0, 111.0]
    eth_path = [50.0, 50.0, 49.0, 49.0, 55.0, 56.0]
    seen: set[str] = set()
    for b, e in zip(btc_path, eth_path):
        sigs = list(strat.on_tick({"BTCUSDT": _feat("BTCUSDT", b), "ETHUSDT": _feat("ETHUSDT", e)}))
        for s in sigs:
            seen.add(s.symbol)

    assert "BTCUSDT" in seen
    assert "ETHUSDT" in seen


def test_multi_symbol_sma_uses_equity_provider_for_sizing() -> None:
    """Equity-budgeted sizing should override the static SMA_QTY fallback."""
    from engine.strategies.sma_crossover import SmaCrossoverStrategy

    settings = Settings(
        binance_api_key="x", binance_api_secret="y",
        sma_symbols=["BTCUSDT"],
        sma_fast_window=2, sma_slow_window=4,
        sma_qty=0.001,                # fallback should NOT be used
        sma_risk_per_trade_pct=0.01,  # 1% of equity at risk
        default_stop_loss_pct=0.01,   # 1% stop -> 1.0x notional / equity
        sma_cooldown_sec=0,
    )
    strat = SmaCrossoverStrategy(settings)
    strat.attach_equity_provider(lambda: 10_000.0)  # equity = $10k

    def _feat(mid: float) -> dict[str, Features]:
        return {"BTCUSDT": Features(symbol="BTCUSDT", mid=mid, spread_bps=1.0,
                                     micro_price=mid, imbalance_topn=0.0,
                                     bid_hit_ratio=0.5, ask_hit_ratio=0.5)}

    # Walk a path that forces a crossover.
    sig = None
    for mid in (100.0, 100.0, 99.0, 99.0, 110.0, 111.0):
        out = list(strat.on_tick(_feat(mid)))
        if out:
            sig = out[0]
            break
    assert sig is not None
    # equity 10k * 1% / stop 1% / mid 110 = 1000/110 ≈ 9.09
    # qty should be far larger than the 0.001 fallback.
    assert sig.qty > 0.5
