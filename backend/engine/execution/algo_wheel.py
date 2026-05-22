"""Algo wheel — picks an execution mode per parent order.

Decision rule (mirrors the README):

    For a BUY parent:
        bid imbalance > +T  AND  ask-hit ratio > H  -> FRONTLOAD
        ask imbalance > +T  AND  bid-hit ratio > H  -> BACKLOAD
    For a SELL parent: symmetric mirror.

``T`` / ``H`` come from ``Settings`` and per-symbol ``symbol_calibration.json``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from common.config import Settings
from common.enums import AlgoMode, Side
from common.types import ParentOrder

from ..market_data.feature_store import Features
from ..strategies.mm_calibrated import get_symbol_calibration

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WheelConfig:
    """Knobs for the wheel decision rule."""

    imbalance_threshold: float = 0.20
    hit_ratio_threshold: float = 0.60

    @classmethod
    def from_settings(cls, settings: Settings) -> WheelConfig:
        return cls(
            imbalance_threshold=float(settings.imbalance_threshold),
            hit_ratio_threshold=float(settings.hit_ratio_threshold),
        )


class AlgoWheel:
    def __init__(self, config: WheelConfig | None = None) -> None:
        self._config = config or WheelConfig()

    def apply_settings(self, settings: Settings) -> None:
        self._config = WheelConfig.from_settings(settings)

    def config_for(self, symbol: str, settings: Settings) -> WheelConfig:
        cfg = self._config
        cal = get_symbol_calibration(symbol, settings)
        if cal is None:
            return cfg
        updates: dict[str, float] = {}
        if cal.imbalance_threshold is not None:
            updates["imbalance_threshold"] = cal.imbalance_threshold
        if cal.hit_ratio_threshold is not None:
            updates["hit_ratio_threshold"] = cal.hit_ratio_threshold
        return replace(cfg, **updates) if updates else cfg

    def choose(
        self,
        parent: ParentOrder,
        features: Features,
        settings: Settings | None = None,
    ) -> AlgoMode:
        cfg = self.config_for(parent.symbol, settings) if settings is not None else self._config
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
        else:
            if imb < -cfg.imbalance_threshold and bid_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.FRONTLOAD
            elif imb > cfg.imbalance_threshold and ask_hit > cfg.hit_ratio_threshold:
                mode = AlgoMode.BACKLOAD
            else:
                mode = AlgoMode.NORMAL

        logger.info(
            "wheel %s %s -> %s (imb=%.3f bid_hit=%.2f ask_hit=%.2f T=%.2f H=%.2f)",
            parent.symbol,
            parent.side.value,
            mode.value,
            imb,
            bid_hit,
            ask_hit,
            cfg.imbalance_threshold,
            cfg.hit_ratio_threshold,
        )
        return mode
