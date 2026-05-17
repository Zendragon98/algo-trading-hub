from __future__ import annotations

import os

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from engine.market_data.feature_store import Features  # noqa: E402
from engine.strategies.market_making_v2 import MarketMakingV2Strategy  # noqa: E402


def _feat(
    mid: float,
    micro: float,
    imb: float,
    *,
    spread_bps: float = 12.0,
    tape_bid_hits: int = 0,
    tape_ask_hits: int = 0,
) -> dict[str, Features]:
    return {
        "BTCUSDT": Features(
            symbol="BTCUSDT",
            mid=mid,
            spread_bps=spread_bps,
            micro_price=micro,
            imbalance_topn=imb,
            bid_hit_ratio=0.5,
            ask_hit_ratio=0.5,
            tape_bid_hit_count=tape_bid_hits,
            tape_ask_hit_count=tape_ask_hits,
        )
    }


def _settings(**overrides: object) -> Settings:
    base = dict(
        binance_api_key="x",
        binance_api_secret="y",
        mm2_symbols=["BTCUSDT"],
        mm2_entry_tilt=10.0,
        mm2_min_samples=5,
        mm2_min_skew_bps=0.0,
        mm2_tape_confirm=0.0,
        mm2_min_spread_bps=0.0,
        mm2_min_edge_bps=0.0,
        mm2_fee_round_trip_bps=8.0,
        mm2_spread_buffer_bps=0.0,
        mm2_qty=1.0,
        mm2_cooldown_sec=0.0,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_mm2_allows_tight_spread_when_no_explicit_floor() -> None:
    strat = MarketMakingV2Strategy(_settings(mm2_fee_round_trip_bps=8.0))
    f = _feat(
        mid=100.0,
        micro=99.5,
        imb=-0.5,
        spread_bps=1.5,
        tape_bid_hits=8,
        tape_ask_hits=2,
    )
    sigs: list = []
    for _ in range(8):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert len(sigs) == 1
    assert sigs[0].side is Side.BUY


def test_mm2_fade_buy_when_gates_pass() -> None:
    strat = MarketMakingV2Strategy(_settings())
    f = _feat(
        mid=100.0,
        micro=99.5,
        imb=-0.5,
        spread_bps=12.0,
        tape_bid_hits=8,
        tape_ask_hits=2,
    )
    sigs: list = []
    for _ in range(8):
        sigs = list(strat.on_tick(f))
        if sigs:
            break
    assert len(sigs) == 1
    assert sigs[0].side is Side.BUY


def test_mm2_blocks_entry_when_spread_too_tight() -> None:
    strat = MarketMakingV2Strategy(_settings(mm2_min_spread_bps=10.0))
    f = _feat(
        mid=100.0,
        micro=99.5,
        imb=-0.8,
        spread_bps=5.0,
        tape_bid_hits=10,
        tape_ask_hits=1,
    )
    for _ in range(10):
        assert list(strat.on_tick(f)) == []


def test_mm2_blocks_entry_without_tape_confirm() -> None:
    strat = MarketMakingV2Strategy(_settings(mm2_tape_confirm=0.2))
    # Strong positive composite but tape disagrees (buyers lifting offers).
    f = _feat(
        mid=100.0,
        micro=99.5,
        imb=-0.8,
        spread_bps=12.0,
        tape_bid_hits=1,
        tape_ask_hits=10,
    )
    for _ in range(10):
        assert list(strat.on_tick(f)) == []


def test_mm2_profit_exit() -> None:
    strat = MarketMakingV2Strategy(
        _settings(
            mm2_min_exit_profit_bps=5.0,
            mm2_entry_tilt=50.0,
            mm2_min_samples=3,
        )
    )
    strat.attach_position_provider(lambda _sym: 1.0)
    warm = _feat(mid=100.0, micro=100.0, imb=0.0, spread_bps=12.0)
    for _ in range(5):
        list(strat.on_tick(warm))
    state = strat._state_for("BTCUSDT")  # noqa: SLF001
    state.entry_mid = 100.0
    state.position_opened_ts = 0.0
    state.open_side = 1

    sigs = list(strat.on_tick(_feat(mid=100.08, micro=100.08, imb=0.0, spread_bps=12.0)))
    assert len(sigs) == 1
    assert sigs[0].reduce_only is True
    assert "mm2_profit_exit" in sigs[0].reason


def test_mm2_signal_exit_on_composite_revert() -> None:
    strat = MarketMakingV2Strategy(
        _settings(
            mm2_skew_scale=0.0,
            mm2_imbalance_scale=0.0,
            mm2_tape_scale=0.0,
            mm2_entry_tilt=8.0,
            mm2_exit_tilt=3.0,
            mm2_min_samples=3,
            mm2_min_exit_profit_bps=0.0,
            mm2_max_hold_sec=0.0,
        )
    )
    strat.attach_position_provider(lambda _sym: 1.0)
    for _ in range(5):
        list(strat.on_tick(_feat(mid=100.0, micro=100.1, imb=0.0, spread_bps=12.0)))

    sigs = list(strat.on_tick(_feat(mid=100.0, micro=100.0, imb=0.0, spread_bps=12.0)))
    assert len(sigs) == 1
    assert "mm2_signal_exit" in sigs[0].reason
