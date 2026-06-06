"""Live per-strategy hub snapshots for the strategy hub dashboard."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from common.types import Position

from ..position.position_tracker import PositionTracker
from ..position.strategy_ledger import StrategyPositionLedger
from ..strategies.position_sync import side_from_qty
from ..strategies.strategy_base import StrategyBase
from .performance_tracker import PerformanceTracker

_QTY_EPS = 1e-12
_PNL_EPS = 0.01
_ANALYTICS_FLOAT_EPS = 0.0001


@dataclass(slots=True)
class StrategyLegSnapshot:
    symbol: str
    side: Literal["long", "short"]
    size: float
    entry: float
    mark: float
    unrealized_pnl: float


@dataclass(slots=True)
class StrategyPnlSnapshot:
    name: str
    label: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    open_legs: list[StrategyLegSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class StrategyHubSnapshot:
    ts: float
    mode: Literal["single", "all"]
    strategies: list[StrategyPnlSnapshot]
    analytics: dict[str, dict[str, str | float | int | bool | None]]


def _side_label(qty: float) -> Literal["long", "short"]:
    return "long" if qty > 0 else "short"


def _leg_unrealized(qty: float, entry: float, mark: float) -> float:
    if abs(qty) < _QTY_EPS or entry <= 0 or mark <= 0:
        return 0.0
    return (mark - entry) * qty


def _leg_unrealized_venue_aligned(
    strategy_qty: float,
    entry: float,
    pos: Position | None,
) -> float:
    """Prefer Binance ``up`` scaled to the strategy's share of the venue leg."""
    if abs(strategy_qty) < _QTY_EPS:
        return 0.0
    if pos is not None and abs(pos.qty) >= _QTY_EPS:
        strat_side = side_from_qty(strategy_qty)
        pos_side = side_from_qty(pos.qty)
        if strat_side != 0 and strat_side == pos_side and pos.exchange_unrealized_pnl is not None:
            share = min(1.0, abs(strategy_qty) / abs(pos.qty))
            return float(pos.exchange_unrealized_pnl) * share
    mark = pos.mark_price if pos is not None and pos.mark_price > 0 else 0.0
    eff_entry = entry
    if eff_entry <= 0 and pos is not None and pos.avg_entry_price > 0:
        eff_entry = pos.avg_entry_price
    return _leg_unrealized(strategy_qty, eff_entry, mark)


class StrategyHubService:
    """Build hub snapshots and detect material changes for persistence."""

    def __init__(
        self,
        *,
        ledger: StrategyPositionLedger,
        positions: PositionTracker,
        performance: PerformanceTracker,
    ) -> None:
        self._ledger = ledger
        self._positions = positions
        self._performance = performance
        self._last_payload: dict[str, Any] | None = None
        self._last_snapshot: StrategyHubSnapshot | None = None
        self._force_emit = True

    @property
    def last_snapshot(self) -> StrategyHubSnapshot | None:
        return self._last_snapshot

    def mark_strategy_swap(self) -> None:
        self._force_emit = True

    def peek_snapshot(
        self,
        *,
        ts: float,
        strategies: list[StrategyBase],
        multi_mode: bool,
        analytics: dict[str, dict[str, str | float | int | bool | None]],
    ) -> StrategyHubSnapshot:
        """Build a fresh snapshot for API reads without advancing the persistence gate."""
        snapshot = self._build_snapshot(
            ts=ts,
            strategies=strategies,
            multi_mode=multi_mode,
            analytics=analytics,
        )
        self._last_snapshot = snapshot
        return snapshot

    def refresh(
        self,
        *,
        ts: float,
        strategies: list[StrategyBase],
        multi_mode: bool,
        analytics: dict[str, dict[str, str | float | int | bool | None]],
    ) -> tuple[StrategyHubSnapshot, bool]:
        snapshot = self._build_snapshot(
            ts=ts,
            strategies=strategies,
            multi_mode=multi_mode,
            analytics=analytics,
        )
        self._last_snapshot = snapshot
        payload = self._to_payload(snapshot)
        if self._force_emit or self._material_change(payload):
            self._last_payload = payload
            self._force_emit = False
            return snapshot, True
        return snapshot, False

    def _build_snapshot(
        self,
        *,
        ts: float,
        strategies: list[StrategyBase],
        multi_mode: bool,
        analytics: dict[str, dict[str, str | float | int | bool | None]],
    ) -> StrategyHubSnapshot:
        mode: Literal["single", "all"] = "all" if multi_mode else "single"
        realized = self._performance.realized_pnl_by_strategy()
        pnl_rows: list[StrategyPnlSnapshot] = []

        for strat in strategies:
            name = strat.name
            label = strat.display_label or name
            unrealized, legs = self._unrealized_for_strategy(name)
            realized_pnl = realized.get(name, 0.0)
            pnl_rows.append(
                StrategyPnlSnapshot(
                    name=name,
                    label=label,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized,
                    total_pnl=realized_pnl + unrealized,
                    open_legs=legs,
                )
            )

        return StrategyHubSnapshot(
            ts=ts,
            mode=mode,
            strategies=pnl_rows,
            analytics=analytics,
        )

    def _unrealized_for_strategy(
        self,
        strategy: str,
    ) -> tuple[float, list[StrategyLegSnapshot]]:
        book = self._ledger.snapshot().get(strategy, {})
        total = 0.0
        legs: list[StrategyLegSnapshot] = []
        for sym, qty in book.items():
            if abs(qty) < _QTY_EPS:
                continue
            pos = self._positions.get(sym)
            if pos is None or abs(pos.qty) < _QTY_EPS:
                # Phantom leg: ledger drifted past venue truth (close outside the
                # attributed-fill path). Hidden here; reconcile heals the ledger.
                continue
            mark = pos.mark_price if pos is not None and pos.mark_price > 0 else 0.0
            entry = self._ledger.fill_vwap(strategy, sym)
            u = _leg_unrealized_venue_aligned(qty, entry, pos)
            total += u
            legs.append(
                StrategyLegSnapshot(
                    symbol=sym,
                    side=_side_label(qty),
                    size=abs(qty),
                    entry=entry,
                    mark=mark,
                    unrealized_pnl=u,
                )
            )
        legs.sort(key=lambda row: row.symbol)
        return total, legs

    @staticmethod
    def _to_payload(snapshot: StrategyHubSnapshot) -> dict[str, Any]:
        return {
            "mode": snapshot.mode,
            "strategies": [
                {
                    "name": row.name,
                    "label": row.label,
                    "realized_pnl": row.realized_pnl,
                    "unrealized_pnl": row.unrealized_pnl,
                    "total_pnl": row.total_pnl,
                    "open_legs": [
                        {
                            "symbol": leg.symbol,
                            "side": leg.side,
                            "size": leg.size,
                            "entry": leg.entry,
                            "mark": leg.mark,
                            "unrealized_pnl": leg.unrealized_pnl,
                        }
                        for leg in row.open_legs
                    ],
                }
                for row in snapshot.strategies
            ],
            "analytics": snapshot.analytics,
        }

    def _material_change(self, payload: dict[str, Any]) -> bool:
        if self._last_payload is None:
            return True
        prev = self._last_payload
        if payload.get("mode") != prev.get("mode"):
            return True
        if not _analytics_equal(payload.get("analytics"), prev.get("analytics")):
            return True
        prev_rows = {r["name"]: r for r in prev.get("strategies", [])}
        for row in payload.get("strategies", []):
            name = row["name"]
            old = prev_rows.get(name)
            if old is None:
                return True
            for key in ("realized_pnl", "unrealized_pnl", "total_pnl"):
                if abs(float(row[key]) - float(old[key])) >= _PNL_EPS:
                    return True
            if len(row.get("open_legs", [])) != len(old.get("open_legs", [])):
                return True
            for leg, old_leg in zip(row.get("open_legs", []), old.get("open_legs", [])):
                if leg.get("symbol") != old_leg.get("symbol"):
                    return True
                if abs(float(leg.get("unrealized_pnl", 0)) - float(old_leg.get("unrealized_pnl", 0))) >= _PNL_EPS:
                    return True
        if set(prev_rows) != {r["name"] for r in payload.get("strategies", [])}:
            return True
        return False


def _analytics_equal(
    left: dict[str, dict[str, Any]] | None,
    right: dict[str, dict[str, Any]] | None,
) -> bool:
    left = left or {}
    right = right or {}
    if set(left) != set(right):
        return False
    for name, lv in left.items():
        rv = right.get(name, {})
        if set(lv) != set(rv):
            return False
        for key, lval in lv.items():
            rval = rv.get(key)
            if lval == rval:
                continue
            if isinstance(lval, (int, float)) and isinstance(rval, (int, float)):
                if math.isfinite(float(lval)) and math.isfinite(float(rval)):
                    if abs(float(lval) - float(rval)) < _ANALYTICS_FLOAT_EPS:
                        continue
            return False
    return True
