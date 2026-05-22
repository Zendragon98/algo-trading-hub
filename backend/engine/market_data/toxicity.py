"""Composite toxicity score from tape, depletion, markout, and jumps."""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings

from ..strategies.mm_calibrated import get_symbol_calibration
from .book_depletion import DepletionStats
from .markout_tracker import MarkoutStats
from .mid_tracker import MidStats
from .trade_tape import TapeStats


@dataclass(slots=True)
class ToxicityStats:
    toxicity_score: float = 0.0
    is_toxic: bool = False
    flow_direction: float = 0.0


class ToxicityScorer:
    def __init__(self, settings: Settings) -> None:
        self.apply_settings(settings)

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._threshold = float(settings.mm_toxicity_threshold)
        self._vpin_w = float(settings.mm_toxicity_vpin_weight)
        self._large_w = float(settings.mm_toxicity_large_weight)
        self._dep_w = float(settings.mm_toxicity_depletion_weight)
        self._mark_w = float(settings.mm_toxicity_markout_weight)
        self._jump_w = float(settings.mm_toxicity_jump_weight)
        self._vel_w = float(settings.mm_toxicity_tape_vel_weight)
        self._informed_w = float(settings.mm_toxicity_informed_weight)
        self._mark_norm = max(1.0, float(settings.mm_toxicity_markout_norm_bps))
        self._vel_norm = max(1.0, float(settings.mm_toxicity_tape_vel_norm))
        self._vpin_hi = float(settings.mm_toxicity_vpin_informed_high)
        self._vpin_lo = float(settings.mm_toxicity_vpin_informed_low)
        self._dep_min = float(settings.mm_toxicity_depletion_informed_min)

    def score(
        self,
        *,
        symbol: str = "",
        tape: TapeStats,
        depletion: DepletionStats,
        markout: MarkoutStats,
        mid: MidStats,
    ) -> ToxicityStats:
        threshold = self._threshold
        if symbol:
            cal = get_symbol_calibration(symbol, self._settings)
            if cal is not None and cal.toxicity_threshold is not None:
                threshold = float(cal.toxicity_threshold)

        vpin_ext = abs(tape.vpin - 0.5) * 2.0
        large = tape.large_trade_share
        dep = max(depletion.bid_depletion_score, depletion.ask_depletion_score)
        mark = (
            min(1.0, markout.adverse_ewma_bps / self._mark_norm)
            if markout.adverse_ewma_bps > 0
            else 0.0
        )
        jump = 1.0 if mid.jump_active else 0.0
        tape_vel = (
            min(1.0, tape.trades_per_sec / self._vel_norm) if tape.trades_per_sec > 0 else 0.0
        )

        informed = 0.0
        if depletion.ask_depletion_score > self._dep_min and tape.vpin > self._vpin_hi:
            informed += self._informed_w
        if depletion.bid_depletion_score > self._dep_min and tape.vpin < self._vpin_lo:
            informed += self._informed_w

        raw = (
            self._vpin_w * vpin_ext
            + self._large_w * large
            + self._dep_w * dep
            + self._mark_w * mark
            + self._jump_w * jump
            + self._vel_w * tape_vel
            + informed
        )
        score = max(0.0, min(1.0, raw))
        flow = tape.ask_hit_ratio - tape.bid_hit_ratio
        if tape.total_qty <= 0:
            flow = (tape.vpin - 0.5) * 2.0
        return ToxicityStats(
            toxicity_score=score,
            is_toxic=score >= threshold,
            flow_direction=max(-1.0, min(1.0, flow)),
        )
