"""Portfolio aggregation.

The Portfolio is the engine's source of truth for equity, cash, and
exposure. It is read by:
    - the risk manager (to evaluate pre-trade limits)
    - the API layer (to serve the equity card / equity curve)
    - the stop-loss monitor (to compute drawdown vs session start)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import time

from common.enums import EventType
from common.events import Event, EventBus
from common.types import Position

from ..position.position_tracker import PositionTracker

logger = logging.getLogger(__name__)

# Stablecoin assets we treat as 1:1 cash when ``BASE_CURRENCY`` is one of
# them. Both wallets contribute to dashboard equity so users with split
# USDT/USDC balances see their real account value.
_STABLE_CASH_ASSETS = frozenset({"USDT", "USDC"})


@dataclass(slots=True)
class EquityPoint:
    """One sample on the equity curve."""

    ts: float
    equity: float


@dataclass
class PortfolioSnapshot:
    cash: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    gross_notional: float = 0.0
    net_notional: float = 0.0
    positions: list[Position] = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash + self.realized_pnl + self.unrealized_pnl


class Portfolio:
    """Maintains cash + positions + equity curve.

    Cash is held as a per-asset map so partial ``ACCOUNT_UPDATE`` messages
    (Binance only ships the assets that *changed* in each event) merge
    cleanly without zeroing out unreported wallets. ``cash`` collapses the
    map to a single number using ``base_currency``.
    """

    def __init__(
        self,
        bus: EventBus,
        position_tracker: PositionTracker,
        equity_curve_size: int = 256,
        base_currency: str = "USDT",
    ) -> None:
        self._bus = bus
        self._tracker = position_tracker
        self._cash_by_asset: dict[str, float] = {}
        self._equity_curve: list[EquityPoint] = []
        self._curve_size = equity_curve_size
        self._session_start_equity: float = 0.0
        self._base_currency = base_currency.upper()
        self._lock = asyncio.Lock()

    # --- Lifecycle ---

    def seed_balances(self, balances: Mapping[str, float]) -> None:
        """Replace the per-asset cash map (used at engine boot).

        Sets ``session_start_equity`` so drawdown is measured from this
        snapshot. Subsequent updates from the venue should go through
        ``update_asset_balance`` so unreported assets retain their balance.
        """
        self._cash_by_asset = {k.upper(): float(v) for k, v in balances.items()}
        self._session_start_equity = self.snapshot().equity
        logger.info(
            "portfolio seeded cash=%.2f equity=%.2f assets=%s",
            self.cash, self._session_start_equity,
            {k: round(v, 2) for k, v in self._cash_by_asset.items()},
        )

    def seed_cash(self, cash: float) -> None:
        """Compatibility shim: seed a single ``base_currency`` balance.

        Kept for tests and venues that expose only one wallet number. New
        callers should prefer ``seed_balances`` so the stablecoin merge
        rules (USDT + USDC) survive partial updates.
        """
        self.seed_balances({self._base_currency: float(cash)})

    def update_asset_balance(self, asset: str, balance: float) -> None:
        """Refresh a single asset's wallet balance from the venue.

        Only the named asset is mutated; every other asset retains its
        previous balance. This is the merge-friendly counterpart to the
        wholesale ``update_cash`` overwrite below — it is the entry point
        the engine uses for streaming ``ACCOUNT_UPDATE`` events.
        """
        self._cash_by_asset[asset.upper()] = float(balance)

    def update_balances(self, balances: Mapping[str, float]) -> None:
        """Bulk per-asset refresh (e.g. periodic REST resync).

        Equivalent to calling ``update_asset_balance`` for every entry.
        Assets not present in ``balances`` are left untouched, so a partial
        REST response cannot zero out unreported wallets.
        """
        for asset, value in balances.items():
            self.update_asset_balance(asset, value)

    def update_cash(self, cash: float) -> None:
        """Compatibility shim: overwrite the ``base_currency`` balance.

        Behaviour matches the legacy single-cash model. Prefer
        ``update_asset_balance`` for live updates so per-asset merge holds.
        """
        self._cash_by_asset[self._base_currency] = float(cash)

    @property
    def session_start_equity(self) -> float:
        return self._session_start_equity

    def reanchor_session_start_equity_after_drawdown_rearm(self) -> None:
        """Set session drawdown baseline to current equity (operator ``max_drawdown`` rearm).

        Without this, ``PnLTracker.drawdown_pct`` re-trips on the next tick
        while equity is still below the original session-start snapshot.
        """
        eq = self.snapshot().equity
        self._session_start_equity = eq
        logger.info("session_start_equity re-anchored after drawdown rearm: %.2f", eq)

    @property
    def cash(self) -> float:
        """Single-number cash view in ``base_currency`` units.

        For USDT/USDC base currencies we sum both stablecoin wallets so
        users with split balances see their real account value. Other
        bases collapse to that asset's wallet directly.
        """
        if self._base_currency in _STABLE_CASH_ASSETS:
            return sum(self._cash_by_asset.get(a, 0.0) for a in _STABLE_CASH_ASSETS)
        return self._cash_by_asset.get(self._base_currency, 0.0)

    def cash_by_asset(self) -> dict[str, float]:
        """Return a defensive copy of the per-asset wallet map."""
        return dict(self._cash_by_asset)

    # --- Reads ---

    @staticmethod
    def _unrealized_pnl(positions: list[Position], *, use_mark: bool) -> float:
        """Sum open PnL.

        ``use_mark=False`` (default snapshots): prefer Binance ``up`` when present.
        ``use_mark=True`` (live equity curve): derive from tick marks so stale
        ACCOUNT_UPDATE does not freeze the curve.
        """
        total = 0.0
        for p in positions:
            if use_mark and p.mark_price > 0 and p.qty != 0:
                total += (p.mark_price - p.avg_entry_price) * p.qty
            elif p.exchange_unrealized_pnl is not None and p.qty != 0:
                total += p.exchange_unrealized_pnl
            else:
                total += p.unrealized_pnl
        return total

    def snapshot(self, *, use_mark_pnl: bool = False) -> PortfolioSnapshot:
        positions = self._tracker.all()
        unrealized = self._unrealized_pnl(positions, use_mark=use_mark_pnl)
        realized = sum(p.realized_pnl for p in positions)
        gross = sum(p.notional for p in positions)
        net = sum(p.notional * (1 if p.qty > 0 else -1) for p in positions)
        return PortfolioSnapshot(
            cash=self.cash,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            gross_notional=gross,
            net_notional=net,
            positions=positions,
        )

    def equity_curve(self) -> list[EquityPoint]:
        return list(self._equity_curve)

    def _downsample_equity_curve(self) -> None:
        """Keep the full session span in memory without unbounded growth."""
        curve = self._equity_curve
        n = self._curve_size
        if len(curve) <= n:
            return
        last_idx = len(curve) - 1
        self._equity_curve = [
            curve[round(i * last_idx / (n - 1))]
            for i in range(n)
        ]

    # --- Periodic recompute ---

    async def mark_to_market(self, *, use_mark_pnl: bool = False) -> EquityPoint:
        """Recompute equity and append a curve point.

        Called from the engine clock at ~1Hz. Cheap because it only
        re-aggregates already-current position objects.

        When ``use_mark_pnl`` is set (user-data stream stale), unrealized
        PnL is derived from the latest tick marks so the equity curve moves
        with the market instead of freezing on the last ACCOUNT_UPDATE.
        """
        async with self._lock:
            snap = self.snapshot(use_mark_pnl=use_mark_pnl)
            point = EquityPoint(ts=time(), equity=snap.equity)
            self._equity_curve.append(point)
            if len(self._equity_curve) > self._curve_size:
                self._downsample_equity_curve()

        await self._bus.publish(
            Event(
                type=EventType.EQUITY,
                payload={
                    "ts": point.ts,
                    "equity": point.equity,
                    "cash": snap.cash,
                    "realized_pnl": snap.realized_pnl,
                    "unrealized_pnl": snap.unrealized_pnl,
                    "gross_notional": snap.gross_notional,
                    "net_notional": snap.net_notional,
                },
            )
        )
        return point
