"""Shared dataclasses passed between engine modules.

These are deliberately plain dataclasses (not Pydantic models) because
they are hot-path objects allocated thousands of times per second from
the WebSocket feed handler. Pydantic's validation overhead is reserved
for the API boundary in `api/schemas.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from .enums import AlgoMode, OrderStatus, OrderType, PositionSide, Side, Urgency


@dataclass(slots=True)
class Kline:
    """One historical OHLCV candle.

    Returned by `GatewayInterface.klines` so the dashboard can render real
    price history for an open position. Times are seconds since epoch
    (UTC). Volume is in base-asset units (e.g. BTC).
    """

    open_time: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: float


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
class QuoteIntent:
    """Two-sided MM quote request (not routed through VWAP)."""

    symbol: str
    bid_price: float | None
    ask_price: float | None
    bid_qty: float
    ask_qty: float
    reason: str = ""
    strategy_name: str = ""
    reduce_only_bid: bool = False
    reduce_only_ask: bool = False
    # Pricing audit trail (venue mid vs MM reservation mid + spreads).
    venue_mid: float = 0.0
    reservation_mid: float = 0.0
    inventory_ratio: float = 0.0
    bid_half_bps: float = 0.0
    ask_half_bps: float = 0.0
    unrealized_pnl_bps: float = 0.0
    # When set, engine cancels MM quotes and submits reduce-only market flatten.
    flatten_market: bool = False
    ts: float = field(default_factory=time)


@dataclass(slots=True)
class Signal:
    """A strategy's request to take a position.

    `qty` is in base-asset units (e.g. BTC), positive for the requested
    direction. The risk manager may scale or reject the signal before it
    reaches the execution router.

    `group_id` ties multiple signals into a single atomic batch — used
    by pair strategies so the engine can either submit every leg of a
    pair or none of them (preventing a "naked" leg when one side fails
    a venue filter).
    """

    symbol: str
    side: Side
    qty: float
    reason: str                        # human-readable; logged via signal_log_emit
    score: float = 0.0                 # strategy-internal confidence in [0, 1]
    group_id: str | None = None        # legs sharing this id are submitted atomically
    # When True the engine submits a position-reducing parent only (exits / flattens).
    reduce_only: bool = False
    # Originating strategy (multi-strategy mode). ``"__netted__`` for cross-strategy nets.
    strategy_name: str = ""
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
    group_id: str | None = None        # links pair-trade legs across the OMS
    # True when the parent is unwinding an existing position (stop-loss,
    # take-profit, max-drawdown, operator flatten). Propagates onto every
    # child so the venue is told the order can only reduce position size.
    # Binance Futures additionally waives MIN_NOTIONAL for reduce-only.
    reduce_only: bool = False
    urgency: Urgency = Urgency.PASSIVE
    signal_score: float = 0.0
    strategy_name: str = ""


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
    # Mirrors `ParentOrder.reduce_only`. The OMS forwards this onto the
    # venue (`reduceOnly=true` for Binance Futures) so the order is only
    # accepted as a position-reducing trade.
    reduce_only: bool = False


@dataclass(slots=True)
class Fill:
    """An execution report from the exchange.

    Multiple fills can refer to the same `child_id` for partial fills.
    `fee` is in `fee_asset` units (e.g. USDT for a USDT-margined contract).

    `price` is the venue fill price used for PnL accounting (same as the
    exchange-reported execution unless a future adapter adds adjustments).
    `venue_price` duplicates that audit trail for API payloads. `impact_bps`
    is unused (zero); execution quality vs arrival is in parent-level
    slippage metrics.
    """

    child_id: str
    parent_id: str | None
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    fee_asset: str
    # Exchange identifiers / fields (when provided by the venue adapter).
    trade_id: str | None = None
    realized_pnl: float | None = None
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
    # When populated by the exchange adapter (e.g. Binance ACCOUNT_UPDATE /
    # positionRisk), this value is the venue's own unrealized PnL figure.
    # If absent (tests/mocks), we fall back to mark-entry math.
    exchange_unrealized_pnl: float | None = None
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
        if self.exchange_unrealized_pnl is not None:
            return self.exchange_unrealized_pnl
        # Fallback: PnL of an open position relative to its weighted entry.
        # For shorts the sign of (mark - entry) flips because qty is negative.
        return (self.mark_price - self.avg_entry_price) * self.qty

    @property
    def notional(self) -> float:
        return self.mark_price * self.size
