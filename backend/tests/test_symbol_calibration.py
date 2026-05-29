"""Unified symbol calibration loader and AlgoWheel per-symbol thresholds."""

from __future__ import annotations

import json

from common.config import Settings
from engine.execution.algo_wheel import AlgoWheel, WheelConfig
from engine.market_data.symbol_calibration import (
    _resolve_data_path,
    invalidate_cache,
    load_symbol_calibration,
)


def test_load_symbol_calibration_primary_overrides_legacy(tmp_path) -> None:
    legacy = tmp_path / "mm_spread_calibration.json"
    legacy.write_text(
        json.dumps({"symbols": {"BTCUSDT": {"mm": {"half_spread_bps": 9.0}}}}),
        encoding="utf-8",
    )
    primary = tmp_path / "symbol_calibration.json"
    primary.write_text(
        json.dumps({"symbols": {"BTCUSDT": {"mm": {"half_spread_bps": 2.5}}}}),
        encoding="utf-8",
    )
    invalidate_cache()
    cal = load_symbol_calibration(str(primary))["BTCUSDT"]
    assert cal.half_spread_bps == 2.5


def test_load_symbol_calibration_merges_sections(tmp_path) -> None:
    path = tmp_path / "symbol_calibration.json"
    path.write_text(
        json.dumps(
            {
                "symbols": {
                    "BTCUSDT": {
                        "mm": {"half_spread_bps": 2.5, "toxicity_threshold": 0.7},
                        "execution": {
                            "imbalance_threshold": 0.15,
                            "hit_ratio_threshold": 0.65,
                        },
                        "risk": {"spread_wide_floor_bps": 5.0},
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    invalidate_cache()
    cal = load_symbol_calibration(str(path))["BTCUSDT"]
    assert cal.half_spread_bps == 2.5
    assert cal.toxicity_threshold == 0.7
    assert cal.imbalance_threshold == 0.15
    assert cal.hit_ratio_threshold == 0.65
    assert cal.spread_wide_floor_bps == 5.0


def test_algo_wheel_uses_per_symbol_calibration(tmp_path) -> None:
    path = tmp_path / "symbol_calibration.json"
    path.write_text(
        json.dumps(
            {
                "symbols": {
                    "BTCUSDT": {
                        "execution": {
                            "imbalance_threshold": 0.10,
                            "hit_ratio_threshold": 0.55,
                        },
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    settings = Settings(
        imbalance_threshold=0.20,
        hit_ratio_threshold=0.60,
        symbol_calibration_path=str(path),
    )
    wheel = AlgoWheel(WheelConfig.from_settings(settings))
    cfg = wheel.config_for("BTCUSDT", settings)
    assert cfg.imbalance_threshold == 0.10
    assert cfg.hit_ratio_threshold == 0.55
    cfg_eth = wheel.config_for("ETHUSDT", settings)
    assert cfg_eth.imbalance_threshold == 0.20


def test_resolve_data_path_strips_duplicate_data_prefix(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "engine.market_data.symbol_calibration._backend_data_dir",
        lambda: data_dir,
    )
    assert _resolve_data_path("data/mm_spread_calibration.json") == data_dir / "mm_spread_calibration.json"
    assert _resolve_data_path("mm_spread_calibration.json") == data_dir / "mm_spread_calibration.json"
