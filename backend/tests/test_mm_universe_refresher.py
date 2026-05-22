"""MM universe adverse refresh signals."""

from __future__ import annotations

from analytics.mm_universe_refresher import (
    SymbolMicroSnapshot,
    evaluate_adverse_universe,
    should_run_adverse_refresh,
    should_run_periodic_refresh,
)
from common.config import Settings


def _snap(**kwargs: object) -> SymbolMicroSnapshot:
    return SymbolMicroSnapshot(**kwargs)  # type: ignore[arg-type]


def test_periodic_refresh_interval() -> None:
    assert not should_run_periodic_refresh(last_refresh_ts=100.0, refresh_sec=0, now=200.0)
    assert should_run_periodic_refresh(last_refresh_ts=0.0, refresh_sec=60.0, now=100.0)
    assert not should_run_periodic_refresh(last_refresh_ts=50.0, refresh_sec=60.0, now=100.0)


def test_adverse_refresh_cooldown() -> None:
    assert not should_run_adverse_refresh(
        last_adverse_refresh_ts=0.0,
        cooldown_sec=600.0,
        now=100.0,
    )
    assert should_run_adverse_refresh(
        last_adverse_refresh_ts=0.0,
        cooldown_sec=600.0,
        now=700.0,
    )


def test_adverse_markout_triggers() -> None:
    s = Settings(
        mm_universe_adverse_markout_bps=8.0,
        mm_universe_adverse_min_symbols=2,
    )
    sig = evaluate_adverse_universe(
        ["AUSDT", "BUSDT", "CUSDT"],
        {
            "AUSDT": _snap(markout_adverse_ewma_bps=10.0),
            "BUSDT": _snap(markout_adverse_ewma_bps=9.0),
            "CUSDT": _snap(markout_adverse_ewma_bps=1.0),
        },
        settings=s,
        spread_baselines={},
    )
    assert sig is not None
    assert sig.reason == "adverse_markout"
    assert len(sig.symbols) == 2


def test_adverse_spread_blowout() -> None:
    s = Settings(
        mm_universe_adverse_min_symbols=1,
        mm_universe_adverse_spread_widen_mult=1.5,
    )
    sig = evaluate_adverse_universe(
        ["XUSDT"],
        {"XUSDT": _snap(spread_bps=10.0)},
        settings=s,
        spread_baselines={"XUSDT": 4.0},
    )
    assert sig is not None
    assert sig.reason == "spread_blowout"


def test_regime_vol_on_btc() -> None:
    s = Settings(
        mm_universe_adverse_regime_vol_bps=20.0,
        mm_universe_regime_symbols=["BTCUSDT"],
        mm_universe_adverse_min_symbols=3,
    )
    sig = evaluate_adverse_universe(
        ["ETHUSDT"],
        {
            "ETHUSDT": _snap(vol_ewma_bps=5.0),
            "BTCUSDT": _snap(vol_ewma_bps=30.0),
        },
        settings=s,
        spread_baselines={},
    )
    assert sig is not None
    assert sig.reason == "regime_vol"
