"""Algo wheel — picks an execution mode per parent order.

Decision rule (mirrors the README):

    For a BUY parent:
        bid imbalance > +T  AND  ask-hit ratio > 0.6  -> FRONTLOAD
            (book leaning long, buyers aggressive, expect price to grind up;
             grab fills early before the rally extends)
        ask imbalance > +T  AND  bid-hit ratio > 0.6  -> BACKLOAD
            (sellers stacked but bids are getting hit;
             let the price bleed lower into our buys)
        else -> NORMAL

    For a SELL parent: symmetric mirror.

`T` is configurable via `Settings.imbalance_threshold` (added below if
not present). The default of 0.20 is calibrated by `analytics/orderbook_analyzer`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from common.enums import AlgoMode, Side
from common.types import ParentOrder

from ..market_data.feature_store import Features

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WheelConfig:
    """Knobs for the wheel decision rule."""

    imbalance_threshold: float = 0.20    # |imbalance| above this is "stacked"
    hit_ratio_threshold: float = 0.60    # >60% of one side is "aggressive"


class AlgoWheel:
    def __init__(self, config: WheelConfig | None = None) -> None:
        self._config = config or WheelConfig()

    def choose(self, parent: ParentOrder, features: Features) -> AlgoMode:
        cfg = self._config
        imb = features.imbalance_topn
        bid_hit = features.bid_hit_ratio
        ask_hit = features.ask_hit_ratio

        if parent.side is Side.BUY:
            if imb > cfg.imbalance_threshold and ask_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.FRONTLOAD
            elif imb < -cfg.imbalance_threshold and bid_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.BACKLOAD
            else:
                mode = AlgoMode.NORMAL
        else:  # SELL
            if imb < -cfg.imbalance_threshold and bid_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.FRONTLOAD
            elif imb > cfg.imbalance_threshold and ask_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.BACKLOAD
            else:
                mode = AlgoMode.NORMAL

        logger.info(
            "wheel %s %s -> %s (imb=%.3f bid_hit=%.2f ask_hit=%.2f)",
            parent.symbol, parent.side.value, mode.value, imb, bid_hit, ask_hit,
        )
        return mode
