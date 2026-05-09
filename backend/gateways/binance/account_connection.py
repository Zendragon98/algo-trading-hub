"""Read-only account state.

Wallet balance + open positions, fetched on demand via REST. The engine
only consults this on startup (to seed the PositionTracker) and on
operator-initiated refreshes; live updates flow through `OrderConnection`'s
user-data stream instead.
"""

from __future__ import annotations

import logging

from common.types import Position

from .rest_client import BinanceRestClient

logger = logging.getLogger(__name__)


class AccountConnection:
    def __init__(self, rest: BinanceRestClient, base_currency: str) -> None:
        self._rest = rest
        self._base_currency = base_currency.upper()

    async def fetch_balance(self) -> float:
        """Return the wallet balance in the configured base currency."""
        data = await self._rest.account()
        for asset in data.get("assets", []):
            if asset.get("asset", "").upper() == self._base_currency:
                # walletBalance is the realised cash; availableBalance excludes
                # margin held by open positions. We surface the wallet for
                # the equity card on the dashboard.
                return float(asset.get("walletBalance", 0.0))
        logger.warning("base currency %s not found in account", self._base_currency)
        return 0.0

    async def fetch_positions(self) -> list[Position]:
        rows = await self._rest.position_risk()
        positions: list[Position] = []
        for row in rows:
            qty = float(row.get("positionAmt", 0.0))
            if qty == 0.0:
                continue
            positions.append(
                Position(
                    symbol=row["symbol"],
                    qty=qty,
                    avg_entry_price=float(row.get("entryPrice", 0.0)),
                    mark_price=float(row.get("markPrice", 0.0)),
                    realized_pnl=0.0,  # Binance doesn't return this on positionRisk
                )
            )
        return positions
