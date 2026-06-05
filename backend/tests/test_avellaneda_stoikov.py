"""Avellaneda–Stoikov MM pricing."""

import math

import pytest

from common.config import Settings
from engine.market_data.feature_store import Features
from engine.market_data.own_quote_book import OwnBookState
from engine.strategies import mm_core
from engine.strategies.market_making.avellaneda_stoikov import (
    compute_as_half_spread_bps,
    compute_as_reservation,
    effective_liquidity_k,
    resolve_as_params,
)


def test_as_long_inventory_lowers_reservation() -> None:
    params = resolve_as_params(Settings(mm_as_gamma=12.0, mm_as_horizon_sec=300.0))
    res_long, shift_long = compute_as_reservation(100.0, 0.8, 80.0, params)
    res_flat, shift_flat = compute_as_reservation(100.0, 0.0, 80.0, params)
    assert res_long < res_flat == pytest.approx(100.0)
    assert shift_long < 0.0
    assert shift_flat == pytest.approx(0.0)


def test_as_short_inventory_raises_reservation() -> None:
    params = resolve_as_params(Settings(mm_as_gamma=12.0, mm_as_horizon_sec=300.0))
    res_short, shift_short = compute_as_reservation(100.0, -0.8, 80.0, params)
    assert res_short > 100.0
    assert shift_short > 0.0


def test_as_higher_vol_widens_half_spread() -> None:
    params = resolve_as_params(Settings(mm_as_gamma=8.0, mm_as_k=1.5))
    calm = compute_as_half_spread_bps(20.0, params, k=1.5)
    volatile = compute_as_half_spread_bps(120.0, params, k=1.5)
    assert volatile > calm


def test_as_higher_k_tightens_half_spread() -> None:
    params = resolve_as_params(Settings(mm_as_gamma=8.0))
    illiquid = compute_as_half_spread_bps(60.0, params, k=0.8)
    liquid = compute_as_half_spread_bps(60.0, params, k=2.5)
    assert liquid < illiquid


def test_effective_liquidity_k_scales_with_depth() -> None:
    thin = Features(symbol="BTCUSDT", mid=100.0, bid_depth_ratio=0.3, ask_depth_ratio=0.3)
    deep = Features(symbol="BTCUSDT", mid=100.0, bid_depth_ratio=1.0, ask_depth_ratio=1.0)
    assert effective_liquidity_k(deep, base_k=1.5, depth_weight=1.0) > effective_liquidity_k(
        thin, base_k=1.5, depth_weight=1.0
    )


def test_compute_quote_pricing_uses_symmetric_as_spread() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        vol_5m_bps=50.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
    )
    s = Settings(
        mm_as_pricing_enabled=True,
        mm_as_gamma=10.0,
        mm_as_k=1.5,
        mm_quote_half_spread_bps=1.0,
    )
    pricing = mm_core.compute_quote_pricing(feat=feat, settings=s, skew_avg=0.0, inv_ratio=0.0)
    assert pricing.bid_half_bps == pytest.approx(pricing.ask_half_bps)
    assert pricing.bid_price is not None
    assert pricing.ask_price is not None
    assert pricing.ask_price > pricing.bid_price


def test_compute_quote_intent_as_inventory_skew() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        vol_5m_bps=80.0,
        jump_active=False,
        is_toxic=False,
        bid_depth_ratio=1.0,
        ask_depth_ratio=1.0,
        best_bid=99.9,
        best_ask=100.1,
    )
    own = OwnBookState(symbol="BTCUSDT")
    s = Settings(
        mm_as_pricing_enabled=True,
        mm_as_gamma=12.0,
        mm_max_inventory_notional=100.0,
        mm2_max_inventory_notional=100.0,
        mm_inventory_hard_ratio=0.0,
    )
    intent = mm_core.compute_quote_intent(
        feat=feat,
        settings=s,
        own=own,
        position_qty=1.0,
        equity=10_000.0,
        skew_avg=0.0,
        strategy_name="market_making_v2",
    )
    assert intent.inventory_ratio > 0.9
    assert intent.reservation_mid < intent.venue_mid
    assert intent.bid_half_bps == pytest.approx(intent.ask_half_bps)


def test_as_half_spread_matches_formula() -> None:
    params = resolve_as_params(
        Settings(
            mm_as_gamma=4.0,
            mm_as_k=2.0,
            mm_as_horizon_sec=300.0,
            mm_as_vol_period_sec=300.0,
            mm_as_liquidity_spread_scale_bps=3.0,
            mm_as_min_half_spread_bps=0.1,
        )
    )
    vol_bps = 40.0
    g = params.gamma
    k = 2.0
    vol_frac = vol_bps / 10_000.0
    expected_vol = (g / 2.0) * (vol_frac**2) * 1.0 * 10_000.0
    expected_ln = (1.0 / g) * math.log(1.0 + g / k) * params.liquidity_spread_scale_bps
    got = compute_as_half_spread_bps(vol_bps, params, k=k)
    assert got == pytest.approx(expected_vol + expected_ln)
