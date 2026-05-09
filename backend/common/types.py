"""Shared dataclasses passed between engine modules.

These are deliberately plain dataclasses (not Pydantic models) because
they are hot-path objects allocated thousands of times per second from
the WebSocket feed handler. Pydantic's validation overhead is reserved
for the API boundary in `api/schemas.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from .enums import AlgoMode, OrderStatus, OrderType, PositionSide, Side


@dataclass(slots=True)
class Tick:
    """A market data update.

    `bid`, `ask`, `mid` are top-of-book; `last` is the most recent trade
    price (may be `None` if no trade has been seen yet on a fresh book).
    """

    symbol: str
    bid: float
    ask: float
    last: float | None = None
    ts: float = field(default_factory=time)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(slots=True)
class TapeTrade:
    """A trade observed on the public tape.

    `aggressor` records whether the trade lifted the ask (buy-init) or
    hit the bid (sell-init). The trade-tape uses this to compute the
    rolling bid-hit / ask-hit ratio that feeds the AlgoWheel.
    """

    symbol: str
    price: float
    qty: float
    aggressor: Side
    ts: float = field(default_factory=time)


@dataclass(slots=True)
class Signal:
    """A strategy's request to take a position.

    `qty` is in base-asset units (e.g. BTC), positive for the requested
    direction. The risk manager may scale or reject the signal before it
    reaches the execution router.
    """

    symbol: str
    side: Side
    qty: float
    reason: str                        # human-readable, surfaced in the log stream
    score: float = 0.0                 # strategy-internal confidence in [0, 1]
    ts: float = field(default_factory=time)


@dataclass(slots=True)
class ParentOrder:
    """A logical order the engine wants to execute.

    Carries the originating signal so the execution layer can reason
    about urgency (`score`, `max_slippage_bps`). The AlgoWheel decomposes
    this into a sequence of child orders.
    """

    id: str
    symbol: str
    side: Side
    qty: float
    created_at: float = field(default_factory=time)
    max_slippage_bps: float = 5.0
    algo_mode: AlgoMode | None = None  # populated by AlgoWheel
    notes: str = ""                    # used for log lines / dashboard


@dataclass(slots=True)
class ChildOrder:
    """A single venue order produced by the slicer."""

    id: str                            # client_order_id sent to Binance
    parent_id: str
    symbol: str
    side: Side
    qty: float
    price: float | None                # None for market orders
    order_type: OrderType
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    venue_order_id: str | None = None  # Binance orderId once acknowledged
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)


@dataclass(slots=True)
class Fill:
    """An execution report from the exchange.

    Multiple fills can refer to the same `child_id` for partial fills.
    `fee` is in `fee_asset` units (e.g. USDT for a USDT-margined contract).

    `price` is the price the engine uses for downstream PnL accounting.
    On the testnet this is normally adjusted to include synthetic market
    impact so the dashboard reflects what mainnet would look like.
    `venue_price` is preserved as the raw exchange-reported price so the
    OMS / API can show the audit trail unchanged. `impact_bps` records
    the magnitude of the synthetic adjustment.
    """

    child_id: str
    parent_id: str | None
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    fee_asset: str
    ts: float = field(default_factory=time)
    venue_price: float = 0.0
    impact_bps: float = 0.0


@dataclass(slots=True)
class Position:
    """Net position for a single symbol.

    `qty` is signed: positive = long, negative = short. The dashboard
    consumes the unsigned `size` + `side` derived from this object.
    """

    symbol: str
    qty: float = 0.0
    avg_entry_price: float = 0.0
    mark_price: float = 0.0
    realized_pnl: float = 0.0
    updated_at: float = field(default_factory=time)

    @property
    def side(self) -> PositionSide:
        if self.qty > 0:
            return PositionSide.LONG
        if self.qty < 0:
            return PositionSide.SHORT
        return PositionSide.FLAT

    @property
    def size(self) -> float:
        return abs(self.qty)

    @property
    def unrealized_pnl(self) -> float:
        # PnL of an open position relative to its weighted entry. For shorts
        # the sign of (mark - entry) flips because qty is negative.
        return (self.mark_price - self.avg_entry_price) * self.qty

    @property
    def notional(self) -> float:
        return self.mark_price * self.size
