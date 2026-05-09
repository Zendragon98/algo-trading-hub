"""Abstract venue interface.

Every concrete venue (Binance for now) implements `GatewayInterface`.
The engine never talks to a venue directly; this seam is what lets us
plug in a `MockGateway` in tests without touching engine code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from common.types import ChildOrder, Fill, Position, TapeTrade, Tick


@dataclass(slots=True)
class DepthDiff:
    """L2 book diff event from the venue WebSocket.

    `bids` and `asks` are lists of `(price, qty)`. `qty == 0` means the
    level is removed. `final_update_id` is the venue sequence number used
    to drop out-of-order or stale diffs.
    """

    symbol: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    first_update_id: int
    final_update_id: int


# Callbacks the engine registers with the gateway. Awaiting on the engine
# side is fine; the gateway runs them via `await`.
TickCallback = Callable[[Tick], Awaitable[None]]
DepthCallback = Callable[[DepthDiff], Awaitable[None]]
TradeCallback = Callable[[TapeTrade], Awaitable[None]]
FillCallback = Callable[[Fill], Awaitable[None]]
OrderUpdateCallback = Callable[[ChildOrder], Awaitable[None]]


class GatewayInterface(ABC):
    """Minimum surface every venue must expose."""

    # --- Lifecycle ---
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # --- Market data subscriptions ---
    @abstractmethod
    async def subscribe_market_data(
        self,
        symbols: list[str],
        on_tick: TickCallback,
        on_depth: DepthCallback,
        on_trade: TradeCallback,
    ) -> None: ...

    # --- User data subscriptions ---
    @abstractmethod
    async def subscribe_user_data(
        self,
        on_fill: FillCallback,
        on_order_update: OrderUpdateCallback,
    ) -> None: ...

    # --- Order management ---
    @abstractmethod
    async def place_order(self, order: ChildOrder) -> ChildOrder:
        """Submit `order`; returns the order with `venue_order_id` and
        possibly `status=ACK` populated. Raises on hard rejection."""

    @abstractmethod
    async def cancel_order(self, symbol: str, client_order_id: str) -> None: ...

    # --- Account ---
    @abstractmethod
    async def fetch_positions(self) -> list[Position]: ...

    @abstractmethod
    async def fetch_balance(self) -> float:
        """Return wallet balance in `Settings.base_currency`."""

    # --- Reference data ---
    @abstractmethod
    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        """REST snapshot of the L2 book for `symbol`.

        Returns a venue-normalised mapping with keys ``"bids"``, ``"asks"``
        (each a list of ``[price, qty]``) and ``"lastUpdateId"`` so the
        engine's incremental diff loop can synchronise without caring
        which venue produced the snapshot.
        """
