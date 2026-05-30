"""Bundled calibration seeding."""

from __future__ import annotations

from pathlib import Path

from engine.market_data.symbol_calibration import (
    ensure_calibration_files,
    load_symbol_calibration,
)


def test_ensure_calibration_files_seeds_from_bundle(tmp_path, monkeypatch) -> None:
    bundled = Path(__file__).resolve().parent.parent / "calibration_defaults"
    monkeypatch.setattr(
        "engine.market_data.symbol_calibration._backend_data_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "engine.market_data.symbol_calibration._bundled_defaults_dir",
        lambda: bundled,
    )
    seeded = ensure_calibration_files()
    assert len(seeded) >= 1
    cal = load_symbol_calibration("symbol_calibration.json")
    assert "BTCUSDT" in cal
    assert cal["BTCUSDT"].half_spread_bps is not None
