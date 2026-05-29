"""MM universe scoring and report helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from analytics.mm_universe_scanner import (
    MmSymbolScore,
    MmUniverseReport,
    assemble_tiered_universe,
    derive_stability_thresholds,
    load_mm_universe_report,
    report_is_fresh,
    score_mm_candidate,
    write_mm_universe_report,
)
from common.config import Settings


def test_score_rejects_low_volume() -> None:
    score, ok, reason = score_mm_candidate(
        quote_volume=1_000.0,
        median_spread_bps=5.0,
        spread_cv=0.1,
        mid_vol_bps=2.0,
        min_volume=5_000_000.0,
        min_spread_bps=0.8,
        max_spread_bps=20.0,
        max_spread_cv=0.45,
        max_mid_vol_bps=15.0,
        min_edge_bps=4.0,
    )
    assert score == 0.0
    assert not ok
    assert reason == "low_volume"


def test_score_accepts_stable_liquid_market() -> None:
    score, ok, reason = score_mm_candidate(
        quote_volume=50_000_000.0,
        median_spread_bps=6.0,
        spread_cv=0.12,
        mid_vol_bps=3.0,
        min_volume=5_000_000.0,
        min_spread_bps=0.8,
        max_spread_bps=20.0,
        max_spread_cv=0.45,
        max_mid_vol_bps=15.0,
        min_edge_bps=4.0,
    )
    assert ok
    assert reason is None
    assert score > 50.0


def test_score_rejects_insufficient_edge() -> None:
    _, ok, reason = score_mm_candidate(
        quote_volume=50_000_000.0,
        median_spread_bps=2.0,
        spread_cv=0.1,
        mid_vol_bps=2.0,
        min_volume=5_000_000.0,
        min_spread_bps=0.8,
        max_spread_bps=20.0,
        max_spread_cv=0.45,
        max_mid_vol_bps=15.0,
        min_edge_bps=4.0,
    )
    assert not ok
    assert reason == "insufficient_edge"


def test_report_roundtrip(tmp_path: Path) -> None:
    report = MmUniverseReport(
        generated_at="2026-05-22T12:00:00+00:00",
        recommended=["SOLUSDT", "BNBUSDT"],
        rankings=[
            MmSymbolScore(
                symbol="SOLUSDT",
                quote_volume_24h=1e8,
                last_price=150.0,
                median_spread_bps=4.0,
                spread_cv=0.1,
                mid_vol_bps=2.0,
                edge_bps=1.0,
                score=80.0,
                eligible=True,
            ),
        ],
        candidates_scanned=10,
        sample_rounds=5,
    )
    path = write_mm_universe_report(report, tmp_path / "mm_universe_scan.json")
    loaded = load_mm_universe_report(path)
    assert loaded is not None
    assert loaded.recommended == ["SOLUSDT", "BNBUSDT"]
    assert loaded.rankings[0].symbol == "SOLUSDT"


def test_report_freshness() -> None:
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    report = MmUniverseReport(
        generated_at=recent,
        recommended=["BTCUSDT"],
        rankings=[],
        candidates_scanned=1,
        sample_rounds=1,
    )
    assert report_is_fresh(report, 3600.0)
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    stale_report = MmUniverseReport(
        generated_at=old,
        recommended=["BTCUSDT"],
        rankings=[],
        candidates_scanned=1,
        sample_rounds=1,
    )
    assert not report_is_fresh(stale_report, 3600.0)


def test_min_edge_from_settings() -> None:
    from analytics.mm_universe_scanner import _min_edge_bps

    s = Settings(
        symbol_calibration_path="",
        mm_spread_calibration_path="",
        mm_auto_min_edge_bps=0.0,
        mm2_maker_fee_bps=2.0,
        mm2_spread_buffer_bps=2.0,
        mm2_assume_maker_rebate=False,
    )
    assert _min_edge_bps(s) == pytest.approx(6.0)


def test_derive_thresholds_from_percentile() -> None:
    s = Settings(
        mm_auto_max_spread_cv=0.0,
        mm_auto_max_mid_vol_bps=0.0,
        mm_auto_stability_percentile=75.0,
    )
    th = derive_stability_thresholds(
        s,
        spread_cvs=[0.1, 0.12, 0.15, 0.18, 0.2, 0.25, 0.3, 0.4],
        mid_vols=[2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0],
        range_vols_24h=[80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0, 150.0],
        intraday_vols=[1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
    )
    assert th.source == "percentile"
    assert th.max_spread_cv == pytest.approx(0.2625, rel=0.02)
    assert th.max_mid_vol_bps >= 5.0


def test_derive_thresholds_explicit_override() -> None:
    s = Settings(mm_auto_max_spread_cv=0.5, mm_auto_max_mid_vol_bps=20.0)
    th = derive_stability_thresholds(
        s,
        spread_cvs=[0.1, 0.2],
        mid_vols=[3.0, 4.0],
        range_vols_24h=[100.0, 110.0],
        intraday_vols=[1.0, 2.0],
    )
    assert th.source == "override"
    assert th.max_spread_cv == 0.5
    assert th.max_mid_vol_bps == 20.0


def test_effective_mid_vol_uses_24h_range() -> None:
    from analytics.mm_universe_scanner import _effective_mid_vol_bps

    eff, intra = _effective_mid_vol_bps(1.0, 200.0, sample_window_sec=20.0)
    assert intra > 1.0
    assert eff >= intra * 0.9


def test_assemble_tiered_universe_pins_and_midcaps() -> None:
    settings = Settings(
        mm_auto_max_symbols=5,
        mm_auto_pin_symbols=["BTCUSDT", "ETHUSDT"],
        mm_auto_pin_min_quote_volume=1.0,
        mm_auto_midcap_min_quote_volume=1.0,
        mm_auto_pin_min_edge_bps=0.0,
        mm_auto_pin_min_spread_bps=0.3,
    )
    rankings = [
        MmSymbolScore(
            symbol="BTCUSDT",
            quote_volume_24h=1e9,
            last_price=60_000.0,
            median_spread_bps=1.0,
            spread_cv=0.1,
            mid_vol_bps=2.0,
            edge_bps=0.0,
            score=0.0,
            eligible=False,
            reject_reason="insufficient_edge",
        ),
        MmSymbolScore(
            symbol="AVAXUSDT",
            quote_volume_24h=50e6,
            last_price=9.0,
            median_spread_bps=6.0,
            spread_cv=0.1,
            mid_vol_bps=2.0,
            edge_bps=2.0,
            score=90.0,
            eligible=True,
        ),
        MmSymbolScore(
            symbol="ARBUSDT",
            quote_volume_24h=40e6,
            last_price=0.1,
            median_spread_bps=5.0,
            spread_cv=0.1,
            mid_vol_bps=2.0,
            edge_bps=1.0,
            score=80.0,
            eligible=True,
        ),
    ]
    sample_stats = {
        "BTCUSDT": (1.2, 0.1, 2.0),
        "ETHUSDT": (1.5, 0.1, 2.0),
    }
    from analytics.mm_universe_scanner import TickerVolStats

    tickers = {
        "BTCUSDT": TickerVolStats(1e9, 60_000.0, 61_000.0, 59_000.0, 1.0, 50.0),
        "ETHUSDT": TickerVolStats(5e8, 3_000.0, 3_100.0, 2_900.0, 1.0, 40.0),
    }
    selected = assemble_tiered_universe(
        rankings,
        settings,
        ticker_by_sym=tickers,
        sample_stats=sample_stats,
    )
    assert "BTCUSDT" in selected
    assert "AVAXUSDT" in selected
    assert selected.index("BTCUSDT") < selected.index("AVAXUSDT")
