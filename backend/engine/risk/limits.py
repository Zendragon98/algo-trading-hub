"""Static + derived risk limits.

`Limits` is a pure-data view over the relevant subset of `Settings`. We
keep it separate so the risk manager can be exercised in tests without
having to construct a full `Settings` object (which requires .env).
"""

from __future__ import annotations

from dataclasses import dataclass

from common.config import Settings


@dataclass(frozen=True, slots=True)
class Limits:
    max_risk_pct: float
    max_gross_notional: float
    max_drawdown_pct: float
    default_stop_loss_pct: float
    default_take_profit_pct: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "Limits":
        return cls(
            max_risk_pct=settings.max_risk_pct,
            max_gross_notional=settings.max_gross_notional,
            max_drawdown_pct=settings.max_drawdown_pct,
            default_stop_loss_pct=settings.default_stop_loss_pct,
            default_take_profit_pct=settings.default_take_profit_pct,
        )
