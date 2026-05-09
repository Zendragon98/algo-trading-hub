"""Shared enumerations.

Kept dependency-free so every other package can import these without
pulling in pandas / numpy / fastapi.
"""

from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    """Order / position direction.

    Inherits from `str` so the values serialise transparently into JSON
    payloads consumed by the React console (which uses lowercase strings).
    """

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        """+1 for buys, -1 for sells. Used in PnL math."""
        return 1 if self is Side.BUY else -1


class PositionSide(str, Enum):
    """Net direction of a position. Distinct from `Side` because a `BUY`
    can either open a `LONG` or close a `SHORT`."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class OrderType(str, Enum):
    """Subset of Binance Futures order types we actually use."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    """Lifecycle of a child order on the venue."""

    NEW = "new"               # accepted by gateway, not yet acknowledged
    ACK = "ack"               # acknowledged by exchange, working
    PARTIAL = "partial"       # partially filled
    FILLED = "filled"         # fully filled
    CANCELLED = "cancelled"   # cancelled, no further fills
    REJECTED = "rejected"     # rejected by venue (insufficient margin, etc.)


class AlgoMode(str, Enum):
    """How the VWAP slicer distributes child orders across the schedule.

    Decided per parent order by `engine.execution.algo_wheel.AlgoWheel`.
    """

    FRONTLOAD = "frontload"   # weight skewed to the start of the window
    NORMAL = "normal"         # uniform across the window
    BACKLOAD = "backload"     # weight skewed to the end of the window


class EngineStatus(str, Enum):
    """Top-level engine state surfaced on the dashboard.

    Values match `AlgoStatus` in `src/components/algo/mockData.ts` so the
    REST/WS payloads can be consumed without any client-side translation.
    """

    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class LogLevel(str, Enum):
    """Log levels surfaced on the dashboard log stream.

    `SIGNAL` is a custom level we use whenever a strategy emits a trading
    signal so the UI can colour those lines distinctly from `INFO`.
    """

    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SIGNAL = "signal"


class TradingMode(str, Enum):
    """Whether the engine is wired to a paper / demo / testnet account or
    a real-money one.

    The mode is venue-agnostic: it is set independently of the venue's
    own toggles (e.g. ``BINANCE_TESTNET``, IBKR's port, etc.) so that
    cross-venue policies (kill-switch sensitivity, synthetic impact, log
    annotations) can react to it uniformly.
    """

    PAPER = "paper"   # testnet / demo / sandbox — synthetic impact ON by default
    LIVE = "live"    # real money — synthetic impact OFF, louder logging


class EventType(str, Enum):
    """Topics carried by the in-process EventBus."""

    TICK = "tick"                   # market_data update (top-of-book / mid)
    FILL = "fill"                   # an order was filled (whole or partial)
    ORDER_UPDATE = "order"          # any order lifecycle change
    PARENT_UPDATE = "parent"        # parent order created / progressed / completed
    EXECUTION_REPORT = "execution"  # post-trade analytics for a completed parent
    POSITION = "position"           # position snapshot changed
    EQUITY = "equity"               # portfolio mark-to-market changed
    LOG = "log"                     # operator-visible log line
    STATUS = "status"               # engine status changed
