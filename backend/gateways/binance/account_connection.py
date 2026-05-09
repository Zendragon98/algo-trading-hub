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

    async def fetch_balances(self) -> dict[str, float]:
        """Return wallet balance per asset (e.g. ``{"USDT": 100.0, "USDC": 50.0}``).

        Binance USDT-M futures can expose multiple wallet assets. We return
        every reported asset so the engine can merge per-asset
        ``ACCOUNT_UPDATE`` messages without losing the unreported legs.

        Wallet balance excludes unrealized PnL; realized PnL is reflected in
        the wallet as trades settle.
        """
        data = await self._rest.account()
        assets = data.get("assets", [])

        out: dict[str, float] = {}
        for asset in assets:
            sym = str(asset.get("asset", "")).upper()
            if not sym:
                continue
            out[sym] = _asset_balance(asset)
        return out

    async def fetch_balance(self) -> float:
        """Return the summed wallet balance used to seed the portfolio.

        For ``USDT`` / ``USDC`` we sum both stablecoin wallets so users with
        split balances see their real account value. Other base currencies
        return that asset's wallet directly.
        """
        balances = await self.fetch_balances()
        if self._base_currency in {"USDT", "USDC"}:
            total = balances.get("USDT", 0.0) + balances.get("USDC", 0.0)
            if total == 0.0 and "USDT" not in balances and "USDC" not in balances:
                logger.warning("neither USDT nor USDC found in account assets")
            return total
        if self._base_currency not in balances:
            logger.warning("base currency %s not found in account", self._base_currency)
            return 0.0
        return balances[self._base_currency]

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
                    realized_pnl=0.0,  # walletBalance already reflects realized PnL
                    exchange_unrealized_pnl=float(
                        row.get("unRealizedProfit", row.get("unrealizedProfit", 0.0)) or 0.0
                    ),
                )
            )
        return positions


def _asset_balance(asset: dict) -> float:
    """Pick the wallet balance from a Binance Futures account ``assets`` row.

    Falls back to ``marginBalance - unrealizedProfit`` for older / partial
    payloads so the seeded cash never includes unrealized PnL.
    """
    if asset.get("walletBalance") is not None:
        return float(asset.get("walletBalance", 0.0))
    if asset.get("marginBalance") is not None:
        return float(asset.get("marginBalance", 0.0)) - float(asset.get("unrealizedProfit", 0.0))
    return 0.0
