"""Abstract venue interface.

Every concrete venue (Binance for now) implements `GatewayInterface`.
The engine never talks to a venue directly; this seam is what lets us
plug in a `MockGateway` in tests without touching engine code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from common.types import ChildOrder, Fill, Kline, Position, TapeTrade, Tick


@dataclass(slots=True)
class DepthDiff:
    """L2 book diff event from the venue WebSocket.

    `bids` and `asks` are lists of `(price, qty)`. `qty == 0` means the
    level is removed. Binance uses ``U`` / ``u`` / ``pu`` for sequencing;
    other venues may leave ``prev_final_update_id`` unset.
    """

    symbol: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    first_update_id: int
    final_update_id: int
    prev_final_update_id: int | None = None


@dataclass(frozen=True, slots=True)
class SymbolFilters:
    """Venue-side trading rules for one symbol.

    Populated once during `GatewayInterface.connect()` so the engine can
    validate / size orders before placing them. Every field is `None`
    when the venue does not advertise that constraint.

    All values are expressed in the venue's own units: ``step_size`` /
    ``min_qty`` in the base asset, ``tick_size`` / ``min_notional`` in
    the quote asset.
    """

    symbol: str
    step_size: float | None = None       # qty must be a multiple of this
    tick_size: float | None = None       # price must be a multiple of this
    min_qty: float | None = None         # smallest qty the venue accepts
    max_qty: float | None = None         # conservative cap (min of limit + market)
    max_qty_limit: float | None = None   # LOT_SIZE maxQty
    max_qty_market: float | None = None  # MARKET_LOT_SIZE maxQty
    min_notional: float | None = None    # smallest qty * price the venue accepts


# Callbacks the engine registers with the gateway. Awaiting on the engine
# side is fine; the gateway runs them via `await`.
TickCallback = Callable[[Tick], Awaitable[None]]
DepthCallback = Callable[[DepthDiff], Awaitable[None]]
TradeCallback = Callable[[TapeTrade], Awaitable[None]]
FillCallback = Callable[[Fill], Awaitable[None]]
OrderUpdateCallback = Callable[[ChildOrder], Awaitable[None]]
AccountUpdateCallback = Callable[[dict], Awaitable[None]]
QuoteVolume24hCallback = Callable[[str, float], Awaitable[None]]
MarketReconnectCallback = Callable[[list[str]], Awaitable[None]]


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
        *,
        on_quote_volume_24h: QuoteVolume24hCallback | None = None,
        on_reconnect: MarketReconnectCallback | None = None,
    ) -> None:
        """Subscribe to market data.

        Venues that expose rolling 24h quote volume on the public WebSocket
        (e.g. Binance ``!ticker@arr``) should call ``on_quote_volume_24h``
        so the engine can avoid high-frequency REST ``/ticker/24hr`` polls.

        ``on_reconnect`` is invoked after a market WebSocket drops and
        reconnects so the engine can REST-resync order books before applying
        buffered depth diffs.
        """
        ...

    # --- User data subscriptions ---
    @abstractmethod
    async def subscribe_user_data(
        self,
        on_fill: FillCallback,
        on_order_update: OrderUpdateCallback,
        on_account_update: AccountUpdateCallback | None = None,
    ) -> None: ...

    # --- Order management ---
    @abstractmethod
    async def place_order(self, order: ChildOrder) -> ChildOrder:
        """Submit `order`; returns the order with `venue_order_id` and
        possibly `status=ACK` populated. Raises on hard rejection."""

    @abstractmethod
    async def cancel_order(self, symbol: str, client_order_id: str) -> None: ...

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ChildOrder]:
        """Return working orders on the venue. Default: none (mocks / skeletons)."""
        return []

    async def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> ChildOrder | None:
        """Best-effort REST lookup when user-data lag leaves working orders stale.

        Implementations return ``None`` when the order is unknown or unsupported.
        Used by order reconcile to align OMS with venue truth without tripping
        breakers on missed WebSocket updates.
        """
        return None

    async def cancel_all_open_orders(self) -> None:
        """Cancel every open order on the venue."""
        orders = await self.fetch_open_orders()
        for order in orders:
            await self.cancel_order(order.symbol, order.id)

    # --- Reference data cached at connect() ---
    def get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        """Return cached venue trading rules for `symbol`.

        Concrete venues populate this map during ``connect()`` from
        whatever metadata endpoint they expose (Binance ``exchangeInfo``,
        IBKR ``reqContractDetails``, ...). The default returns ``None``
        so test mocks and not-yet-implemented venues stay permissive.
        """
        return None

    # --- Margin / leverage controls (futures venues) ---
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Configure leverage for `symbol` on the venue.

        No-op on spot venues, IBKR (where leverage is account-wide), and
        test mocks. The Binance USDT-M Futures adapter overrides this to
        call ``POST /fapi/v1/leverage`` before the first entry order for a
        symbol so stop-loss-budgeted sizing has enough margin headroom.
        """
        return None

    # --- Account ---
    @abstractmethod
    async def fetch_positions(self) -> list[Position]: ...

    @abstractmethod
    async def fetch_balance(self) -> float:
        """Return wallet balance in `Settings.base_currency`."""

    async def fetch_balances(self) -> dict[str, float]:
        """Return wallet balance per asset (e.g. ``{"USDT": 100.0, "USDC": 50.0}``).

        The default implementation falls back to ``fetch_balance()`` keyed by
        ``Settings.base_currency`` so existing venues (mocks, IBKR skeleton)
        keep working without changes. Venues that hold multiple stable assets
        (Binance USDT-M Futures keeps USDT *and* USDC wallets) override this so
        the engine can merge per-asset ``ACCOUNT_UPDATE`` messages without
        zeroing out unreported assets.
        """
        # Lazy import avoids a circular reference; this is the only place the
        # gateway interface needs to know about Settings, and only at runtime.
        from common.config import get_settings

        balance = await self.fetch_balance()
        return {get_settings().base_currency.upper(): balance}

    async def fetch_balances_and_positions(
        self,
    ) -> tuple[dict[str, float], list[Position]]:
        """Wallet map + open positions in as few REST calls as the venue allows.

        Default chains ``fetch_balances`` + ``fetch_positions``. Binance USDT-M
        overrides with one ``GET /fapi/v2/account`` to halve reconcile weight.
        """
        balances = await self.fetch_balances()
        positions = await self.fetch_positions()
        return balances, positions

    async def fetch_24h_volumes(self, symbols: list[str]) -> dict[str, float]:
        """Return 24h notional (quote-asset) volume per symbol.

        Used by strategies that weight a consensus reference by liquidity
        (e.g. pairs trading). Default returns ``{}`` so non-Binance gateways
        and test mocks degrade gracefully — the consumer must fall back to
        equal weights when the cache is empty.
        """
        return {}

    # --- Clock sync (signed REST) ---
    async def sync_clock(self) -> None:
        """Align local timestamps with the venue clock (no-op on mocks)."""
        return None

    def clock_skew_ms(self) -> float:
        """Signed offset: venue_time_ms - local_time_ms (0 when unknown)."""
        return 0.0

    # --- Reference data ---
    @abstractmethod
    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        """REST snapshot of the L2 book for `symbol`.

        Returns a venue-normalised mapping with keys ``"bids"``, ``"asks"``
        (each a list of ``[price, qty]``) and ``"lastUpdateId"`` so the
        engine's incremental diff loop can synchronise without caring
        which venue produced the snapshot.
        """

    @abstractmethod
    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list[Kline]:
        """Return the most recent `limit` historical OHLCV candles.

        ``interval`` follows the Binance convention (``"1m"``, ``"5m"``,
        ``"15m"``, ``"1h"``, ``"4h"``, ...); other venues map their own
        bar sizes to the closest match. Used by the dashboard's position
        chart so it never has to fabricate price history.
        """
