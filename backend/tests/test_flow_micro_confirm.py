"""Flow microstructure confirm helpers."""

from __future__ import annotations

from common.config import Settings
from common.enums import Side
from engine.market_data.feature_store import Features
from engine.strategies.flow_micro_confirm import (
    depth_confirms_direction,
    depth_replenished,
    micro_entry_blocked,
    micro_exit_depth_replenish,
    micro_exit_toxic_flip,
    micro_score_boost,
    micro_size_multiplier,
    toxic_flow_aligned,
)
from engine.strategies.flow_momentum import FlowMomentumStrategy


def _settings(**overrides: object) -> Settings:
    base = {
        "flow_symbols": ["BTCUSDT"],
        "flow_tape_threshold": 0.10,
        "flow_imbalance_min": 0.05,
        "flow_confirm_ticks": 3,
        "flow_cooldown_sec": 0.0,
        "flow_qty": 0.01,
        "flow_skip_toxic": False,
        "flow_require_depletion": False,
        "flow_min_tape_velocity": 0.0,
    }
    base.update(overrides)
    return Settings.model_validate(base)


def _feat(**kwargs: object) -> Features:
    tape = float(kwargs.pop("tape", 0.0))  # type: ignore[arg-type]
    imb = float(kwargs.pop("imb", 0.0))  # type: ignore[arg-type]
    mid = float(kwargs.pop("mid", 100.0))  # type: ignore[arg-type]
    ask = 0.5 + tape / 2.0
    bid = 1.0 - ask
    return Features(
        symbol="BTCUSDT",
        mid=mid,
        spread_bps=float(kwargs.pop("spread_bps", 3.0)),  # type: ignore[arg-type]
        imbalance_topn=imb,
        bid_hit_ratio=bid,
        ask_hit_ratio=ask,
        tape_velocity=2.0,
        **{k: v for k, v in kwargs.items() if k != "dep"},
        depth_depletion_asym=float(kwargs.get("dep", 0.0)),  # type: ignore[arg-type]
    )


def _settings_micro(**overrides: object) -> Settings:
    return _settings(
        flow_micro_boost_enabled=True,
        flow_require_depletion=False,
        flow_confirm_ticks=3,
        flow_jump_skip_entry=True,
        **overrides,
    )


def test_toxic_aligned_long() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_flow_direction=0.25,
    )
    assert toxic_flow_aligned(feat, 1, min_align=0.12)


def test_depth_confirms_long_when_ask_depleted() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, ask_depth_ratio=0.20, bid_depth_ratio=0.90)
    s = _settings_micro()
    assert depth_confirms_direction(feat, 1, s)


def test_depth_replenished_detects_refill_after_sweep() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, ask_depth_ratio=0.90)
    s = _settings_micro()
    assert depth_replenished(feat, 1, s)
    assert depth_replenished(Features(symbol="BTCUSDT", mid=100.0, ask_depth_ratio=1.0), 1, s) is False


def test_depth_boost_increases_size_when_confirmed() -> None:
    base = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_score=0.0,
        toxicity_flow_direction=0.0,
        ask_depth_ratio=0.90,
    )
    depleted = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_score=0.0,
        toxicity_flow_direction=0.0,
        ask_depth_ratio=0.20,
    )
    s = _settings_micro()
    assert micro_size_multiplier(depleted, 1, s) > micro_size_multiplier(base, 1, s)


def test_depth_replenish_exits_long_when_tape_faded() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, ask_depth_ratio=0.90)
    s = _settings_micro(flow_exit_tape_frac=0.45, flow_tape_threshold=0.10)
    assert micro_exit_depth_replenish(
        feat, 1, s, tape=0.02, tape_thr=0.10, exit_tape_frac=0.45
    )


def test_micro_blocks_jump() -> None:
    feat = Features(symbol="BTCUSDT", mid=100.0, jump_active=True)
    s = _settings_micro()
    assert micro_entry_blocked(feat, 1, s) == "jump"


def test_micro_blocks_misaligned_toxic() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_score=0.70,
        toxicity_flow_direction=-0.30,
    )
    s = _settings_micro()
    assert micro_entry_blocked(feat, 1, s) == "toxic_misalign"


def test_micro_boosts_aligned_size_and_score() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_score=0.65,
        toxicity_flow_direction=0.30,
        large_trade_share=0.20,
        vpin=0.62,
    )
    s = _settings_micro()
    assert micro_size_multiplier(feat, 1, s) > 1.05
    assert micro_score_boost(feat, 1, s) > 0.02


def test_toxic_flip_exits_long() -> None:
    feat = Features(
        symbol="BTCUSDT",
        mid=100.0,
        toxicity_score=0.55,
        toxicity_flow_direction=-0.35,
    )
    s = _settings_micro()
    assert micro_exit_toxic_flip(feat, 1, s)


def test_flow_entry_boosted_when_toxic_aligned() -> None:
    strat = FlowMomentumStrategy(_settings_micro())
    strat.attach_position_provider(lambda _s: 0.0)
    base_feat = _feat(tape=0.20, imb=0.12)
    base_feat.toxicity_score = 0.60
    base_feat.toxicity_flow_direction = 0.25
    base_feat.large_trade_share = 0.20
    base_feat.vpin = 0.60
    feats = {"BTCUSDT": base_feat}
    signals = []
    for _ in range(3):
        signals = list(strat.on_tick(feats))
    assert len(signals) == 1
    assert signals[0].side is Side.BUY
    assert "tox=" in signals[0].reason


def test_flow_skips_misaligned_toxic_entry() -> None:
    strat = FlowMomentumStrategy(_settings_micro())
    strat.attach_position_provider(lambda _s: 0.0)
    feat = _feat(tape=0.20, imb=0.12)
    feat.toxicity_score = 0.70
    feat.toxicity_flow_direction = -0.30
    for _ in range(5):
        assert list(strat.on_tick({"BTCUSDT": feat})) == []


def test_flow_toxic_flip_exit_signal() -> None:
    strat = FlowMomentumStrategy(_settings_micro())
    strat.attach_position_provider(lambda _s: 0.01)
    state = strat._state_for("BTCUSDT", 3)
    state.fill_vwap = 100.0
    state.entry_ts = __import__("time").time() - 5.0
    state.open_side = 1
    feat = _feat(tape=0.15, imb=0.10, mid=100.02)
    feat.toxicity_score = 0.55
    feat.toxicity_flow_direction = -0.35
    signals = list(strat.on_tick({"BTCUSDT": feat}))
    assert len(signals) == 1
    assert "flow_toxic_flip" in signals[0].reason
