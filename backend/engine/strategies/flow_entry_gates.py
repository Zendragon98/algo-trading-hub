"""Flow momentum entry filters (spread/fee floor, rising tape)."""

from __future__ import annotations

from collections.abc import Sequence

from common.config import Settings

from ..market_data.feature_store import Features


def entry_spread_ok(feat: Features, settings: Settings) -> bool:
    """Reject entries when spread + fees consume the stop budget."""
    spread = feat.spread_bps
    if spread is None:
        return True
    sl = float(settings.flow_stop_loss_bps)
    frac = float(settings.flow_max_spread_entry_frac)
    cap = float(settings.flow_max_spread_entry_bps)
    if cap <= 0 and sl > 0 and frac > 0:
        cap = sl * frac
    if cap > 0 and spread > cap:
        return False
    fee_rt = float(settings.flow_taker_fee_bps) * 2.0
    min_edge = float(settings.flow_min_edge_bps)
    if min_edge > 0 and spread + fee_rt > min_edge and sl > 0 and spread > sl * 0.5:
        return False
    return True


def tape_rising(recent_tape: Sequence[float], direction: int) -> bool:
    """True when signed tape strength is non-decreasing over the window."""
    if direction == 0 or len(recent_tape) < 2:
        return True
    signed = [float(t) * direction for t in recent_tape]
    return signed[-1] >= signed[0]
