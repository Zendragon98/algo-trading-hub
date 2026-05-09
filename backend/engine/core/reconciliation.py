"""Periodic position + balance reconciliation against the venue.

When the user-data WebSocket is active (``ORDER_TRADE_UPDATE``,
``ACCOUNT_UPDATE``), local wallet + position state is already driven by
the same stream Binance recommends instead of REST polling. In that
mode (``reconcile_skip_rest_when_user_data_fresh``) this reconciler
skips GET ``/account`` and GET ``/positionRisk`` until user-data has been
idle longer than ``reconcile_user_data_fresh_sec``, then performs a REST
snapshot again for drift detection.

If REST runs: pull authoritative balances + positions, diff qty vs
``PositionTracker``, refresh portfolio wallets. Mismatch above
``RECONCILE_QTY_TOLERANCE`` trips MAJOR ``reconcile_mismatch``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from common.config import Settings
from gateways.gateway_interface import GatewayInterface

from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker
from ..risk.circuit_breaker import (
    Breach,
    BreakerScope,
    BreakerSeverity,
    CircuitBreaker,
)

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(
        self,
        gateway: GatewayInterface,
        positions: PositionTracker,
        portfolio: Portfolio,
        breaker: CircuitBreaker,
        interval_sec: float,
        qty_tolerance: float,
        skip_rest_poll: Callable[[], bool] | None = None,
    ) -> None:
        self._gateway = gateway
        self._positions = positions
        self._portfolio = portfolio
        self._breaker = breaker
        self._interval = max(5.0, interval_sec)
        self._qty_tolerance = max(0.0, qty_tolerance)
        self._skip_rest_poll = skip_rest_poll
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        gateway: GatewayInterface,
        positions: PositionTracker,
        portfolio: Portfolio,
        breaker: CircuitBreaker,
        skip_rest_poll: Callable[[], bool] | None = None,
    ) -> "Reconciler":
        return cls(
            gateway=gateway,
            positions=positions,
            portfolio=portfolio,
            breaker=breaker,
            interval_sec=settings.reconcile_interval_sec,
            qty_tolerance=settings.reconcile_qty_tolerance,
            skip_rest_poll=skip_rest_poll,
        )

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="engine-reconcile")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    async def _run(self) -> None:
        # Seed the baseline once before kicking off the diff loop. This
        # avoids a spurious mismatch on the very first iteration when the
        # WS user-data hasn't fully populated yet.
        try:
            await asyncio.sleep(self._interval)
            await self.reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("reconcile baseline failed; continuing")

        while True:
            try:
                await asyncio.sleep(self._interval)
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("reconcile failed; will retry next interval")

    async def reconcile_once(self) -> None:
        """One pass: refresh balances + diff positions vs the venue."""
        if self._skip_rest_poll is not None and self._skip_rest_poll():
            logger.debug(
                "reconcile skipped (user-data WebSocket recently active; no REST snapshot)",
            )
            return

        try:
            balances = await self._gateway.fetch_balances()
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetch_balances failed during reconcile")
            backoff = getattr(exc, "retry_after_sec", None)
            if backoff is not None:
                await asyncio.sleep(min(float(backoff) + 1.0, 86_400.0))
                return
        else:
            if balances:
                self._portfolio.update_balances(balances)

        try:
            venue_positions = await self._gateway.fetch_positions()
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetch_positions failed during reconcile")
            backoff = getattr(exc, "retry_after_sec", None)
            if backoff is not None:
                await asyncio.sleep(min(float(backoff) + 1.0, 86_400.0))
            return

        venue_by_symbol = {p.symbol: p for p in venue_positions}
        local_by_symbol = {p.symbol: p for p in self._positions.all()}
        symbols = set(venue_by_symbol) | set(local_by_symbol)
        for symbol in symbols:
            venue_qty = (
                venue_by_symbol[symbol].qty if symbol in venue_by_symbol else 0.0
            )
            local_qty = (
                local_by_symbol[symbol].qty if symbol in local_by_symbol else 0.0
            )
            if abs(venue_qty - local_qty) <= self._qty_tolerance:
                continue
            logger.error(
                "reconcile mismatch on %s: venue=%.10f local=%.10f",
                symbol, venue_qty, local_qty,
            )
            self._breaker.trip(
                Breach(
                    code="reconcile_mismatch",
                    scope=BreakerScope.ENGINE,
                    severity=BreakerSeverity.MAJOR,
                    detail=f"{symbol} venue={venue_qty:.6f} local={local_qty:.6f}",
                )
            )
