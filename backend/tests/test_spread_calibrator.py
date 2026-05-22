"""L2 spread calibration from synthetic snapshots."""

import json

import pandas as pd

from analytics.l2_store import merge_l2_snapshots
from analytics.spread_calibrator import build_calibration, write_calibration
from common.config import Settings
from engine.market_data.mm_spread_calibration import load_spread_calibration
from engine.strategies.mm_symbol_params import resolve_mm_params


def test_calibrate_and_resolve_half_spread(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("analytics.l2_store.backend_data_root", lambda: tmp_path)
    sym = "BTCUSDT"
    rows = [
        {
            "ts": float(i),
            "symbol": sym,
            "best_bid": 100.0,
            "best_ask": 100.02,
            "mid": 100.01,
            "spread_bps": 2.0,
            "bid_depth_top_n": 10.0,
            "ask_depth_top_n": 10.0,
            "imbalance_top_n": 0.0,
            "last_update_id": i,
        }
        for i in range(50)
    ]
    merge_l2_snapshots(pd.DataFrame(rows), sym)

    report = build_calibration(
        [sym],
        settings=Settings(
            mm_spread_calib_min_samples=30,
            mm_quote_use_venue_spread_floor=False,
        ),
    )
    out = tmp_path / "cal.json"
    write_calibration(report, out)

    cal = load_spread_calibration(str(out))
    assert sym in cal
    assert cal[sym].half_spread_bps >= 1.0

    params = resolve_mm_params(
        sym,
        Settings(
            mm_spread_calibration_path=str(out),
            mm_quote_use_venue_spread_floor=False,
        ),
    )
    assert params.half_spread_bps == cal[sym].half_spread_bps
