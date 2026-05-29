"""Per-symbol funding rate cache for MM carry adjustment."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class FundingSnapshot:
    rate_bps: float = 0.0
    carry_bps: float = 0.0
    updated_ts: float = 0.0


class FundingRateStore:
    def __init__(self) -> None:
        self._by_symbol: dict[str, FundingSnapshot] = {}

    def update(
        self,
        symbol: str,
        *,
        rate: float,
        next_funding_ts: float | None = None,
        hold_sec: float = 3600.0,
    ) -> None:
        sym = symbol.upper()
        rate_bps = rate * 10_000.0
        carry_bps = rate_bps
        if next_funding_ts is not None and next_funding_ts > time():
            frac = min(1.0, max(0.0, (next_funding_ts - time()) / max(hold_sec, 1.0)))
            carry_bps = rate_bps * frac
        self._by_symbol[sym] = FundingSnapshot(
            rate_bps=rate_bps,
            carry_bps=carry_bps,
            updated_ts=time(),
        )

    def get(self, symbol: str) -> FundingSnapshot | None:
        return self._by_symbol.get(symbol.upper())

    def rate_bps(self, symbol: str) -> float:
        snap = self.get(symbol)
        return snap.rate_bps if snap is not None else 0.0

    def carry_bps(self, symbol: str) -> float:
        snap = self.get(symbol)
        return snap.carry_bps if snap is not None else 0.0
