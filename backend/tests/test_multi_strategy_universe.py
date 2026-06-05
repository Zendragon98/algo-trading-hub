"""Tests for STRATEGY=all universe partition."""

from __future__ import annotations

from unittest.mock import patch

from common.config import Settings
from common.multi_strategy_universe import partition_multi_strategy_universe


def test_partition_assigns_disjoint_tiers() -> None:
    settings = Settings(
        strategy="all",
        multi_strategy_partition=True,
        mm_auto_max_symbols=3,
        sma_max_symbols=2,
        blend_max_symbols=2,
        flow_max_symbols=2,
        mm2_symbols=["AUTO"],
        sma_symbols=["AUTO"],
        blend_symbols=["AUTO"],
        flow_symbols=["AUTO"],
        symbols=["AUTO"],
    )
    candidates = [
        "AAAUSDT",
        "BBBUSDT",
        "CCCUSDT",
        "DDDUSDT",
        "EEEUSDT",
        "FFFUSDT",
        "GGGUSDT",
        "HHHUSDT",
        "IIIUSDT",
    ]

    with patch(
        "common.multi_strategy_universe.load_mm_universe_report",
        return_value=type("R", (), {"recommended": candidates})(),
    ):
        resolved = partition_multi_strategy_universe(settings)

    assert resolved.mm2_symbols == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert resolved.sma_symbols == ["DDDUSDT", "EEEUSDT"]
    assert resolved.blend_symbols == ["FFFUSDT", "GGGUSDT"]
    assert resolved.flow_symbols == ["HHHUSDT", "IIIUSDT"]

    all_syms = (
        resolved.mm2_symbols
        + resolved.sma_symbols
        + resolved.blend_symbols
        + resolved.flow_symbols
    )
    assert len(all_syms) == len(set(all_syms))

    pairs = resolved.pair_legs()
    assert len(pairs) >= 3
    assert all(u.endswith("USDT") and c.endswith("USDC") for u, c in pairs)


def test_partition_skipped_when_not_all_mode() -> None:
    settings = Settings(strategy="flow", flow_symbols=["BTCUSDT"], mm2_symbols=["BTCUSDT"])
    with patch(
        "common.multi_strategy_universe.load_mm_universe_report",
        return_value=type("R", (), {"recommended": ["BTCUSDT", "ETHUSDT"]})(),
    ):
        resolved = partition_multi_strategy_universe(settings)
    assert resolved.flow_symbols == ["BTCUSDT"]
    assert resolved.mm2_symbols == ["BTCUSDT"]
