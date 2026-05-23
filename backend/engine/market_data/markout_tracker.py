"""Post-fill markout tracking for adverse selection gating and audit logs."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass

from common.enums import Side
from common.types import Fill

logger = logging.getLogger(__name__)

MarkoutListener = Callable[["MarkoutObservation"], None]

_DEFAULT_HORIZONS_SEC = (1.0, 5.0, 30.0)


@dataclass(slots=True)
class MarkoutStats:
    adverse_ewma_bps: float = 0.0
    last_fill_adverse_bps: float = 0.0
    fill_count: int = 0


@dataclass(slots=True)
class MarkoutObservation:
    symbol: str
    side: str
    fill_price: float
    mid_at_fill: float
    mid_at_horizon: float
    horizon_sec: float
    signed_bps: float
    adverse_bps: float
    favorable: bool
    parent_id: str
    strategy_name: str
    child_id: str
    fill_ts: float
    observed_ts: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _PendingMarkout:
    side: Side
    fill_price: float
    mid_at_fill: float
    ts: float
    parent_id: str
    strategy_name: str
    child_id: str
    horizons_done: set[float]


class MarkoutTracker:
    def __init__(
        self,
        alpha: float = 0.15,
        *,
        horizons_sec: tuple[float, ...] = _DEFAULT_HORIZONS_SEC,
        listener: MarkoutListener | None = None,
    ) -> None:
        self._alpha = max(1e-6, min(alpha, 1.0))
        self._horizons = tuple(h for h in horizons_sec if h > 0)
        self._listener = listener
        self._pending: dict[str, deque[_PendingMarkout]] = {}
        self._adverse_ewma: dict[str, float] = {}
        self._last_adverse: dict[str, float] = {}
        self._fill_count: dict[str, int] = {}

    def set_listener(self, listener: MarkoutListener | None) -> None:
        self._listener = listener

    def on_fill(
        self,
        symbol: str,
        fill: Fill,
        mid_at_fill: float,
        ts: float,
        *,
        strategy_name: str = "",
    ) -> None:
        if mid_at_fill <= 0 or fill.price <= 0:
            return
        q = self._pending.setdefault(symbol, deque(maxlen=128))
        q.append(
            _PendingMarkout(
                side=fill.side,
                fill_price=fill.price,
                mid_at_fill=mid_at_fill,
                ts=ts,
                parent_id=fill.parent_id or "",
                strategy_name=strategy_name,
                child_id=fill.child_id or "",
                horizons_done=set(),
            )
        )
        self._fill_count[symbol] = self._fill_count.get(symbol, 0) + 1
        logger.info(
            "MM fill %s %s qty=%.8f @ %.8f mid=%.8f parent=%s strategy=%s "
            "(markout pending %ss)",
            symbol,
            fill.side.value,
            fill.qty,
            fill.price,
            mid_at_fill,
            fill.parent_id or "-",
            strategy_name or "-",
            ",".join(str(int(h)) if h == int(h) else str(h) for h in self._horizons),
        )

    def on_mid(self, symbol: str, mid: float, ts: float) -> None:
        q = self._pending.get(symbol)
        if not q or mid <= 0:
            return
        for item in list(q):
            for horizon in self._horizons:
                if horizon in item.horizons_done:
                    continue
                if ts - item.ts < horizon:
                    continue
                item.horizons_done.add(horizon)
                signed = _signed_markout_bps(item.side, item.fill_price, mid)
                adverse = max(0.0, signed)
                if adverse > 0:
                    prev = self._adverse_ewma.get(symbol, 0.0)
                    self._adverse_ewma[symbol] = (
                        self._alpha * adverse + (1.0 - self._alpha) * prev
                    )
                    self._last_adverse[symbol] = adverse
                obs = MarkoutObservation(
                    symbol=symbol,
                    side=item.side.value,
                    fill_price=item.fill_price,
                    mid_at_fill=item.mid_at_fill,
                    mid_at_horizon=mid,
                    horizon_sec=horizon,
                    signed_bps=signed,
                    adverse_bps=adverse,
                    favorable=signed <= 0,
                    parent_id=item.parent_id,
                    strategy_name=item.strategy_name,
                    child_id=item.child_id,
                    fill_ts=item.ts,
                    observed_ts=ts,
                )
                self._emit(obs)

    def _emit(self, obs: MarkoutObservation) -> None:
        tag = "favorable" if obs.favorable else "ADVERSE"
        logger.info(
            "MM markout %s %s @ %.8f horizon=%.0fs signed=%+.2f bps %s "
            "mid_fill=%.8f mid_now=%.8f parent=%s strategy=%s",
            obs.symbol,
            obs.side,
            obs.fill_price,
            obs.horizon_sec,
            obs.signed_bps,
            tag,
            obs.mid_at_fill,
            obs.mid_at_horizon,
            obs.parent_id or "-",
            obs.strategy_name or "-",
        )
        if self._listener is not None:
            try:
                self._listener(obs)
            except Exception:  # noqa: BLE001
                logger.exception("markout listener failed for %s", obs.symbol)

    def stats(self, symbol: str) -> MarkoutStats:
        return MarkoutStats(
            adverse_ewma_bps=self._adverse_ewma.get(symbol, 0.0),
            last_fill_adverse_bps=self._last_adverse.get(symbol, 0.0),
            fill_count=self._fill_count.get(symbol, 0),
        )


def _signed_markout_bps(side: Side, fill_price: float, mid: float) -> float:
    """Positive = adverse for the fill side."""
    if side is Side.BUY:
        return (mid - fill_price) / fill_price * 10_000.0
    return (fill_price - mid) / fill_price * 10_000.0
