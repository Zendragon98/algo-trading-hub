"""Simulated fills and position tracking for offline backtests."""

from __future__ import annotations

from dataclasses import dataclass, field

from common.enums import Side
from common.types import Signal
from engine.strategies.strategy_base import StrategyBase


@dataclass(slots=True)
class SimFill:
    symbol: str
    side: str
    qty: float
    price: float
    ts: float
    reason: str
    pnl: float = 0.0
    action: str = "open"


@dataclass(slots=True)
class SimState:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    avg_entry: dict[str, float] = field(default_factory=dict)
    fills: list[SimFill] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def qty(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def mark_equity(self, marks: dict[str, float]) -> float:
        equity = self.cash
        for sym, qty in self.positions.items():
            mark = marks.get(sym, self.avg_entry.get(sym, 0.0))
            equity += qty * mark
        self.equity_curve.append(equity)
        return equity


class FillSimulator:
    def __init__(self, *, initial_equity: float, slippage_bps: float) -> None:
        self._slippage = slippage_bps / 10_000.0
        self.state = SimState(cash=initial_equity)

    def apply_signals(
        self,
        signals: list[Signal],
        marks: dict[str, float],
        strategy: StrategyBase,
    ) -> None:
        for sig in sorted(signals, key=lambda s: (s.group_id or "", s.symbol)):
            mark = marks.get(sig.symbol)
            if mark is None or mark <= 0:
                continue
            self._fill_one(sig, mark, strategy)

    def _fill_one(self, sig: Signal, mark: float, strategy: StrategyBase) -> None:
        side = sig.side
        slip = self._slippage
        price = mark * (1.0 + slip) if side is Side.BUY else mark * (1.0 - slip)
        qty = abs(sig.qty)
        if qty <= 0:
            return
        pos = self.state.qty(sig.symbol)
        pnl = 0.0
        action = "open"

        if sig.reduce_only or (pos > 0 and side is Side.SELL) or (pos < 0 and side is Side.BUY):
            close_qty = min(abs(pos), qty)
            if close_qty <= 0:
                return
            entry = self.state.avg_entry.get(sig.symbol, price)
            if pos > 0:
                pnl = (price - entry) * close_qty
                self.state.positions[sig.symbol] = pos - close_qty
            else:
                pnl = (entry - price) * close_qty
                self.state.positions[sig.symbol] = pos + close_qty
            self.state.cash += pnl
            action = "close"
            qty = close_qty
            if abs(self.state.positions.get(sig.symbol, 0.0)) < 1e-12:
                self.state.positions.pop(sig.symbol, None)
                self.state.avg_entry.pop(sig.symbol, None)
        else:
            signed = qty if side is Side.BUY else -qty
            prev = pos
            new_pos = prev + signed
            notional = price * qty
            if prev == 0:
                self.state.avg_entry[sig.symbol] = price
                if side is Side.BUY:
                    self.state.cash -= notional
                else:
                    self.state.cash += notional
            elif (prev > 0 and signed > 0) or (prev < 0 and signed < 0):
                total = abs(prev) + qty
                if total > 0:
                    self.state.avg_entry[sig.symbol] = (
                        abs(prev) * self.state.avg_entry.get(sig.symbol, price) + qty * price
                    ) / total
                if side is Side.BUY:
                    self.state.cash -= notional
                else:
                    self.state.cash += notional
            else:
                self.state.avg_entry[sig.symbol] = price
                if side is Side.BUY:
                    self.state.cash -= notional
                else:
                    self.state.cash += notional
            self.state.positions[sig.symbol] = new_pos

        self.state.fills.append(
            SimFill(
                symbol=sig.symbol,
                side=side.value,
                qty=qty,
                price=price,
                ts=sig.ts,
                reason=sig.reason,
                pnl=pnl,
                action=action,
            )
        )
        strategy.on_fill(sig.symbol, qty if side is Side.BUY else -qty, side.value)
