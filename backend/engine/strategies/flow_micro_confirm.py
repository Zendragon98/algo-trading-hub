"""Toxicity / informed-flow helpers for flow_momentum (soft confirm, not hard alpha)."""

from __future__ import annotations

from common.config import Settings

from ..market_data.feature_store import Features


def toxic_flow_aligned(feat: Features, direction: int, *, min_align: float) -> bool:
    """True when composite toxicity flow direction agrees with entry side."""
    if direction == 0 or min_align <= 0:
        return False
    flow = float(feat.toxicity_flow_direction)
    if direction > 0:
        return flow >= min_align
    return flow <= -min_align


def micro_entry_blocked(
    feat: Features,
    direction: int,
    settings: Settings,
) -> str | None:
    """Optional entry veto from jump latch or fighting informed flow."""
    if direction == 0:
        return None
    if bool(getattr(settings, "flow_jump_skip_entry", True)) and feat.jump_active:
        return "jump"
    if not bool(getattr(settings, "flow_micro_boost_enabled", True)):
        return None
    tox = float(feat.toxicity_score)
    align_min = float(getattr(settings, "flow_toxic_align_min", 0.12))
    exhaust = float(getattr(settings, "flow_toxic_exhaust_score", 0.92))
    aligned = toxic_flow_aligned(feat, direction, min_align=align_min)
    if (
        bool(getattr(settings, "flow_toxic_misalign_skip", True))
        and tox >= float(getattr(settings, "flow_toxic_misalign_min_score", 0.55))
        and not aligned
    ):
        return "toxic_misalign"
    if tox >= exhaust and not aligned:
        return "toxic_exhaust"
    return None


def micro_size_multiplier(
    feat: Features,
    direction: int,
    settings: Settings,
) -> float:
    """Scale entry size when informed flow confirms tape (1.0 = no change)."""
    if not bool(getattr(settings, "flow_micro_boost_enabled", True)) or direction == 0:
        return 1.0
    align_min = float(getattr(settings, "flow_toxic_align_min", 0.12))
    if not toxic_flow_aligned(feat, direction, min_align=align_min):
        return 1.0
    max_boost = max(1.0, float(getattr(settings, "flow_toxic_size_boost_max", 1.30)))
    tox = float(feat.toxicity_score)
    if tox < 0.30:
        mult = 1.0
    else:
        # Ramp size between moderate and strong aligned toxicity.
        t = min(1.0, (tox - 0.30) / 0.45)
        mult = 1.0 + t * (max_boost - 1.0)
    large_min = float(getattr(settings, "flow_large_trade_boost_min", 0.15))
    if large_min > 0 and float(feat.large_trade_share) >= large_min:
        mult = min(max_boost * 1.05, mult * 1.08)
    vpin_ext = abs(float(feat.vpin) - 0.5) * 2.0
    if vpin_ext >= 0.25:
        mult = min(max_boost * 1.05, mult * (1.0 + 0.04 * min(1.0, (vpin_ext - 0.25) / 0.35)))
    exhaust = float(getattr(settings, "flow_toxic_exhaust_score", 0.92))
    if tox >= 0.80 and tox < exhaust:
        mult *= 0.92
    return mult


def micro_score_boost(
    feat: Features,
    direction: int,
    settings: Settings,
) -> float:
    """Additive boost to signal score for urgent/cross-touch routing."""
    if not bool(getattr(settings, "flow_micro_boost_enabled", True)) or direction == 0:
        return 0.0
    align_min = float(getattr(settings, "flow_toxic_align_min", 0.12))
    if not toxic_flow_aligned(feat, direction, min_align=align_min):
        return 0.0
    cap = max(0.0, float(getattr(settings, "flow_toxic_score_boost_max", 0.10)))
    tox = float(feat.toxicity_score)
    boost = cap * min(1.0, max(0.0, (tox - 0.25) / 0.55))
    large_min = float(getattr(settings, "flow_large_trade_boost_min", 0.15))
    if large_min > 0 and float(feat.large_trade_share) >= large_min:
        boost = min(cap, boost + cap * 0.25)
    return boost


def micro_exit_toxic_flip(
    feat: Features,
    pos_side: int,
    settings: Settings,
) -> bool:
    """True when toxicity flow has flipped against the open position."""
    if pos_side == 0 or not bool(getattr(settings, "flow_exit_toxic_flip", True)):
        return False
    flip_min = float(getattr(settings, "flow_exit_toxic_flip_min", 0.20))
    tox_min = float(getattr(settings, "flow_exit_toxic_flip_score_min", 0.40))
    if float(feat.toxicity_score) < tox_min:
        return False
    flow = float(feat.toxicity_flow_direction)
    if pos_side > 0:
        return flow <= -flip_min
    return flow >= flip_min
