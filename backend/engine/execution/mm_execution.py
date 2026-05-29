"""MM execution helpers: no-cross clamp, zones, ladder/climb targets."""

from __future__ import annotations

import time
from dataclasses import dataclass

from common.config import Settings
from common.enums import MmExecutionMode, Side

from ..strategies.mm_core import infer_price_tick


@dataclass(slots=True)
class LevelTarget:
    level_index: int
    price: float
    qty: float


def _bps_factor(bps: float) -> float:
    return bps / 10_000.0


def clamp_targets_no_cross(
    bid: float | None,
    ask: float | None,
    *,
    best_bid: float | None,
    best_ask: float | None,
    tick: float,
) -> tuple[float | None, float | None]:
    if tick <= 0:
        return bid, ask
    if bid is not None and best_ask is not None and best_ask > 0 and bid >= best_ask:
        bid = best_ask - tick
    if ask is not None and best_bid is not None and best_bid > 0 and ask <= best_bid:
        ask = best_bid + tick
    return bid, ask


def min_place_bid(best_bid: float, place_range_bps: float) -> float:
    if place_range_bps <= 0:
        return 0.0
    return best_bid * (1.0 - _bps_factor(place_range_bps))


def max_place_ask(best_ask: float, place_range_bps: float) -> float:
    if place_range_bps <= 0:
        return float("inf")
    return best_ask * (1.0 + _bps_factor(place_range_bps))


def min_keep_bid(best_bid: float, cancel_range_bps: float) -> float:
    if cancel_range_bps <= 0:
        return 0.0
    return best_bid * (1.0 - _bps_factor(cancel_range_bps))


def max_keep_ask(best_ask: float, cancel_range_bps: float) -> float:
    if cancel_range_bps <= 0:
        return float("inf")
    return best_ask * (1.0 + _bps_factor(cancel_range_bps))


def within_place_zone(
    side: Side,
    price: float,
    *,
    best_bid: float | None,
    best_ask: float | None,
    place_range_bps: float,
) -> bool:
    if place_range_bps <= 0:
        return True
    if side is Side.BUY:
        if best_bid is None or best_bid <= 0:
            return False
        return price >= min_place_bid(best_bid, place_range_bps)
    if best_ask is None or best_ask <= 0:
        return False
    return price <= max_place_ask(best_ask, place_range_bps)


def cancel_reason(
    side: Side,
    order_price: float,
    target: float,
    *,
    best_bid: float | None,
    best_ask: float | None,
    cancel_range_bps: float,
    posted_ts: float,
    min_rest_sec: float,
    now: float,
) -> str | None:
    if side is Side.BUY:
        if order_price > target + 1e-12:
            return "too_aggressive"
        if cancel_range_bps > 0 and best_bid is not None and best_bid > 0:
            if order_price < min_keep_bid(best_bid, cancel_range_bps) - 1e-12:
                return "too_deep"
    else:
        if order_price < target - 1e-12:
            return "too_aggressive"
        if cancel_range_bps > 0 and best_ask is not None and best_ask > 0:
            if order_price > max_keep_ask(best_ask, cancel_range_bps) + 1e-12:
                return "too_deep"
    if min_rest_sec > 0 and posted_ts > 0 and now - posted_ts < min_rest_sec:
        return None
    if min_rest_sec > 0 and posted_ts > 0 and now - posted_ts >= min_rest_sec * 3:
        move_bps = abs(target - order_price) / order_price * 10_000.0 if order_price > 0 else 0.0
        if move_bps > 0.5:
            return "stale"
    return None


def chase_should_replace(
    working_price: float,
    working_qty: float,
    target_price: float,
    target_qty: float,
    refresh_bps: float,
) -> bool:
    if working_price <= 0 or working_qty <= 0:
        return True
    move_bps = abs(target_price - working_price) / working_price * 10_000.0
    if move_bps >= refresh_bps:
        return True
    if working_qty > 0 and abs(target_qty - working_qty) / working_qty >= 0.05:
        return True
    return False


def climb_next_price(
    side: Side,
    working_price: float,
    target: float,
    *,
    tick: float,
    climb_ticks: int,
) -> float | None:
    if tick <= 0 or climb_ticks <= 0:
        return target
    step = tick * climb_ticks
    if side is Side.BUY:
        if working_price > target + 1e-12:
            return max(target, working_price - step)
        if working_price < target - 1e-12:
            return min(target, working_price + step)
        return working_price
    if working_price < target - 1e-12:
        return min(target, working_price + step)
    if working_price > target + 1e-12:
        return max(target, working_price - step)
    return working_price


def ladder_level_targets(
    side: Side,
    target: float,
    total_qty: float,
    *,
    tick: float,
    levels: int,
    spacing_ticks: int,
    weights: list[float],
) -> list[LevelTarget]:
    n = max(1, levels)
    spacing = max(1, spacing_ticks) * tick
    w = weights if len(weights) == n else [1.0 / n] * n
    total_w = sum(w) or 1.0
    out: list[LevelTarget] = []
    for i in range(n):
        if side is Side.BUY:
            price = target - i * spacing
        else:
            price = target + i * spacing
        qty = total_qty * (w[i] / total_w)
        if price > 0 and qty > 0:
            out.append(LevelTarget(level_index=i, price=price, qty=qty))
    return out


def parse_ladder_weights(spec: str, levels: int) -> list[float]:
    n = max(1, levels)
    raw = (spec or "equal").strip().lower()
    if raw == "equal" or not raw:
        return [1.0 / n] * n
    parts = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if len(parts) != n:
        return [1.0 / n] * n
    return parts


def resolve_execution_mode(
    intent_mode: MmExecutionMode,
    settings: Settings,
    *,
    side: Side,
    take_flag: bool,
) -> MmExecutionMode:
    if take_flag:
        return MmExecutionMode.TAKE
    if intent_mode is not MmExecutionMode.MAKE:
        return intent_mode
    default = (settings.mm_execution_mode or "make").strip().lower()
    if side is Side.BUY:
        side_raw = (settings.mm_execution_mode_bid or default).strip().lower()
    else:
        side_raw = (settings.mm_execution_mode_ask or default).strip().lower()
    try:
        return MmExecutionMode(side_raw)
    except ValueError:
        return MmExecutionMode.MAKE


def tick_from_feat(best_bid: float | None, best_ask: float | None, mid: float, spread_bps: float | None) -> float:
    from ..market_data.feature_store import Features

    feat = Features(
        symbol="",
        mid=mid if mid > 0 else None,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=spread_bps,
    )
    return infer_price_tick(feat)
