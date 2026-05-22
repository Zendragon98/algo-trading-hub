"""Composite toxicity score from tape, depletion, markout, and jumps."""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings

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
        self._threshold = float(settings.mm_toxicity_threshold)

    def score(
        self,
        *,
        tape: TapeStats,
        depletion: DepletionStats,
        markout: MarkoutStats,
        mid: MidStats,
    ) -> ToxicityStats:
        vpin_ext = abs(tape.vpin - 0.5) * 2.0
        large = tape.large_trade_share
        dep = max(depletion.bid_depletion_score, depletion.ask_depletion_score)
        mark = min(1.0, markout.adverse_ewma_bps / 20.0) if markout.adverse_ewma_bps > 0 else 0.0
        jump = 1.0 if mid.jump_active else 0.0
        tape_vel = min(1.0, tape.trades_per_sec / 50.0) if tape.trades_per_sec > 0 else 0.0

        informed = 0.0
        if depletion.ask_depletion_score > 0.5 and tape.vpin > 0.55:
            informed += 0.25
        if depletion.bid_depletion_score > 0.5 and tape.vpin < 0.45:
            informed += 0.25

        raw = (
            0.2 * vpin_ext
            + 0.15 * large
            + 0.2 * dep
            + 0.2 * mark
            + 0.15 * jump
            + 0.05 * tape_vel
            + informed
        )
        score = max(0.0, min(1.0, raw))
        flow = (tape.ask_hit_ratio - tape.bid_hit_ratio)
        if tape.total_qty <= 0:
            flow = (tape.vpin - 0.5) * 2.0
        return ToxicityStats(
            toxicity_score=score,
            is_toxic=score >= self._threshold,
            flow_direction=max(-1.0, min(1.0, flow)),
        )
