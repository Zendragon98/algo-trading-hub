"""Flow momentum AUTO universe (full MM scan universe)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from analytics.mm_universe_scanner import (
    MmSymbolScore,
    MmUniverseReport,
    assemble_pin_universe,
    assemble_tiered_universe,
    resolve_flow_universe,
)
from common.config import Settings


def _settings(**kwargs: object) -> Settings:
    return Settings.model_validate(
        {
            "mm_auto_pin_symbols": ["BTCUSDT", "ETHUSDT"],
            "mm_auto_max_symbols": 4,
            **kwargs,
        },
    )


def test_assemble_pin_universe_excludes_midcaps() -> None:
    settings = _settings()
    rankings = [
        MmSymbolScore(
            symbol="BTCUSDT",
            quote_volume_24h=1e9,
            last_price=50000.0,
            median_spread_bps=2.0,
            spread_cv=0.1,
            mid_vol_bps=5.0,
            edge_bps=1.0,
            score=10.0,
            eligible=True,
        ),
        MmSymbolScore(
            symbol="MEUSDT",
            quote_volume_24h=15e6,
            last_price=1.0,
            median_spread_bps=15.0,
            spread_cv=0.2,
            mid_vol_bps=20.0,
            edge_bps=8.0,
            score=50.0,
            eligible=True,
        ),
    ]
    pins = assemble_pin_universe(rankings, settings)
    tiered = assemble_tiered_universe(rankings, settings)
    assert "BTCUSDT" in pins
    assert "MEUSDT" not in pins
    assert "MEUSDT" in tiered


@pytest.mark.asyncio
async def test_resolve_flow_universe_uses_full_mm_universe() -> None:
    settings = _settings()
    report = MmUniverseReport(
        generated_at="2026-01-01T00:00:00Z",
        recommended=["BTCUSDT", "ETHUSDT", "MEUSDT"],
        rankings=[],
        candidates_scanned=0,
        sample_rounds=0,
    )
    with patch(
        "analytics.mm_universe_scanner._load_or_scan_mm_report",
        new=AsyncMock(return_value=report),
    ):
        flow = await resolve_flow_universe(settings)
    assert flow == ["BTCUSDT", "ETHUSDT", "MEUSDT"]


def test_needs_auto_includes_flow_symbols() -> None:
    from common.universe_bootstrap import needs_auto_universe_resolve

    s = Settings(venue="binance", flow_symbols=["AUTO"])
    assert needs_auto_universe_resolve(s)
