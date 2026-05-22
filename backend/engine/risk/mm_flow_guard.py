"""MM-specific pre-trade flow guards (jump, toxicity, depletion)."""

from __future__ import annotations

from common.config import Settings

from ..market_data.feature_store import Features
from .circuit_breaker import Breach, BreakerScope, BreakerSeverity


class MmFlowGuard:
    def __init__(self, settings: Settings) -> None:
        self.apply_settings(settings)

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._cooldown = float(settings.breaker_minor_cooldown_sec)
        self._depletion_breaker = float(settings.mm_depletion_breaker_ratio)

    def evaluate_entry(self, feat: Features, *, reduce_only: bool) -> Breach | None:
        if reduce_only:
            return None
        sym = feat.symbol
        if feat.jump_active:
            return Breach(
                code="price_jump",
                scope=BreakerScope.SYMBOL,
                severity=BreakerSeverity.MINOR,
                target=sym,
                cooldown_sec=self._cooldown,
                detail=f"return_1s={feat.mid_return_1s_bps:.1f}bps",
            )
        if feat.is_toxic:
            return Breach(
                code="toxic_flow",
                scope=BreakerScope.SYMBOL,
                severity=BreakerSeverity.MINOR,
                target=sym,
                cooldown_sec=self._cooldown,
                detail=f"score={feat.toxicity_score:.2f}",
            )
        if self._depletion_breaker > 0:
            if feat.bid_depth_ratio < self._depletion_breaker:
                return Breach(
                    code="book_depleted",
                    scope=BreakerScope.SYMBOL,
                    severity=BreakerSeverity.MINOR,
                    target=sym,
                    cooldown_sec=self._cooldown,
                    detail=f"bid_depth_ratio={feat.bid_depth_ratio:.2f}",
                )
            if feat.ask_depth_ratio < self._depletion_breaker:
                return Breach(
                    code="book_depleted",
                    scope=BreakerScope.SYMBOL,
                    severity=BreakerSeverity.MINOR,
                    target=sym,
                    cooldown_sec=self._cooldown,
                    detail=f"ask_depth_ratio={feat.ask_depth_ratio:.2f}",
                )
        return None
