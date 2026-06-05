"""Avellaneda–Stoikov inventory-aware market making formulas.

Reservation price (inventory adjustment):
    r_t = S_t - q_t * gamma * sigma^2 * (T - t)

Optimal half-spread (spread adjustment):
    delta_t = (gamma / 2) * sigma^2 * (T - t) + (1 / gamma) * ln(1 + gamma / k)

Bid / ask:
    p_bid = r_t - delta_t
    p_ask = r_t + delta_t

Units: ``inventory_ratio`` is normalized signed position in [-1, 1] (q / cap).
Volatility is 5-minute realized vol in bps; horizon ``T - t`` is in seconds and
scaled against ``vol_period_sec`` (default 300s) so sigma^2 * tau is dimensionless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common.config import Settings

from ...market_data.feature_store import Features


@dataclass(frozen=True, slots=True)
class AsPricingParams:
    gamma: float
    k: float
    horizon_sec: float
    vol_period_sec: float
    vol_floor_bps: float
    liquidity_spread_scale_bps: float
    min_half_spread_bps: float


def resolve_as_params(settings: Settings) -> AsPricingParams:
    return AsPricingParams(
        gamma=max(1e-9, float(settings.mm_as_gamma)),
        k=max(1e-9, float(settings.mm_as_k)),
        horizon_sec=max(0.0, float(settings.mm_as_horizon_sec)),
        vol_period_sec=max(1.0, float(settings.mm_as_vol_period_sec)),
        vol_floor_bps=max(0.0, float(settings.mm_as_vol_floor_bps)),
        liquidity_spread_scale_bps=max(0.0, float(settings.mm_as_liquidity_spread_scale_bps)),
        min_half_spread_bps=max(0.1, float(settings.mm_as_min_half_spread_bps)),
    )


def as_vol_bps(feat: Features, params: AsPricingParams) -> float:
    raw = float(feat.vol_5m_bps or 0.0)
    if raw <= 0:
        raw = float(feat.vol_1h_bps or 0.0)
    return max(params.vol_floor_bps, raw)


def effective_liquidity_k(
    feat: Features,
    *,
    base_k: float,
    depth_weight: float,
) -> float:
    """Higher k (more liquid) -> narrower spreads."""
    depth = min(
        max(0.0, float(feat.bid_depth_ratio)),
        max(0.0, float(feat.ask_depth_ratio)),
    )
    w = max(0.0, min(1.0, depth_weight))
    return base_k * (1.0 - w + w * depth)


def compute_as_reservation(
    mid: float,
    inventory_ratio: float,
    vol_bps: float,
    params: AsPricingParams,
) -> tuple[float, float]:
    """Return (reservation_mid, inventory_shift_bps). Long inventory lowers r_t."""
    if mid <= 0:
        return 0.0, 0.0
    vol_frac = vol_bps / 10_000.0
    tau = params.horizon_sec / params.vol_period_sec
    inv_shift_frac = inventory_ratio * params.gamma * (vol_frac**2) * tau
    reservation = mid * (1.0 - inv_shift_frac)
    inv_shift_bps = -inv_shift_frac * 10_000.0
    return reservation, inv_shift_bps


def compute_as_half_spread_bps(
    vol_bps: float,
    params: AsPricingParams,
    *,
    k: float,
) -> float:
    """Symmetric optimal half-spread in bps (wider when vol or gamma rise; tighter when k rises)."""
    vol_frac = vol_bps / 10_000.0
    tau = params.horizon_sec / params.vol_period_sec
    g = params.gamma
    k_eff = max(1e-9, k)
    vol_term_bps = (g / 2.0) * (vol_frac**2) * tau * 10_000.0
    ln_term_bps = (1.0 / g) * math.log(1.0 + g / k_eff) * params.liquidity_spread_scale_bps
    return max(params.min_half_spread_bps, vol_term_bps + ln_term_bps)


def compute_as_quote_pricing(
    *,
    mid: float,
    inventory_ratio: float,
    vol_bps: float,
    params: AsPricingParams,
    k: float,
) -> tuple[float, float, float, float]:
    """Return reservation_mid, inventory_shift_bps, half_spread_bps, half_spread_bps."""
    reservation, inv_shift_bps = compute_as_reservation(
        mid, inventory_ratio, vol_bps, params
    )
    half = compute_as_half_spread_bps(vol_bps, params, k=k)
    return reservation, inv_shift_bps, half, half
