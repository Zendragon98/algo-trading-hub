"""Working-order exposure caps (institutional circuit breakers).

    - ``max_active_orders``: block when working child count is at cap
    - ``max_open_order_notional_usd``: block when outstanding order
      notional would exceed cap
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from common.config import Settings
from common.types import ChildOrder


def _child_open_notional(
    child: ChildOrder,
    mid_for: Callable[[str], float | None],
) -> float:
    remaining = max(0.0, child.qty - child.filled_qty)
    if remaining <= 0:
        return 0.0
    px = child.price
    if px is None or px <= 0:
        px = mid_for(child.symbol)
    if px is None or px <= 0:
        return 0.0
    return remaining * px


class OrderExposureGuard:
    def __init__(
        self,
        working_children: Callable[[], Iterable[ChildOrder]],
        mid_for_symbol: Callable[[str], float | None],
        *,
        max_active_orders: int = 0,
        max_open_order_notional_usd: float = 0.0,
    ) -> None:
        self._working = working_children
        self._mid_for = mid_for_symbol
        self._max_orders = max(0, int(max_active_orders))
        self._max_notional = max(0.0, float(max_open_order_notional_usd))

    def apply_settings(self, settings: Settings) -> None:
        self._max_orders = max(0, int(settings.max_active_orders))
        self._max_notional = max(0.0, float(settings.max_open_order_notional_usd))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        working_children: Callable[[], Iterable[ChildOrder]],
        mid_for_symbol: Callable[[str], float | None],
    ) -> OrderExposureGuard:
        return cls(
            working_children=working_children,
            mid_for_symbol=mid_for_symbol,
            max_active_orders=settings.max_active_orders,
            max_open_order_notional_usd=settings.max_open_order_notional_usd,
        )

    def check(
        self,
        symbol: str,
        qty: float,
        price: float | None = None,
    ) -> tuple[bool, str]:
        working = list(self._working())
        if self._max_orders > 0 and len(working) >= self._max_orders:
            return False, "max_active_orders"

        if self._max_notional > 0:
            open_n = sum(_child_open_notional(c, self._mid_for) for c in working)
            px = price if price and price > 0 else self._mid_for(symbol)
            add = qty * px if px and px > 0 else 0.0
            if open_n + add > self._max_notional + 1e-9:
                return False, "max_open_order_notional"
        return True, ""
