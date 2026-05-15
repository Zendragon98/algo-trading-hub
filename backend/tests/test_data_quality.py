"""DataQualityMonitor depth sequencing."""

from __future__ import annotations

from unittest.mock import MagicMock

from engine.market_data.data_quality import DataQualityMonitor, DiffAction
from gateways.gateway_interface import DepthDiff


def _monitor() -> DataQualityMonitor:
    return DataQualityMonitor(breaker=MagicMock(), crossed_book_breaker=False)


def test_assess_drop_stale_diff() -> None:
    mon = _monitor()
    diff = DepthDiff(
        symbol="BTCUSDT",
        bids=[],
        asks=[],
        first_update_id=1,
        final_update_id=10,
    )
    action, gap = mon.assess(diff, book_ready=True, book_last_update_id=10)
    assert action is DiffAction.DROP_STALE
    assert gap == 0


def test_assess_gap_on_first_update_id() -> None:
    mon = _monitor()
    diff = DepthDiff(
        symbol="BTCUSDT",
        bids=[],
        asks=[],
        first_update_id=15,
        final_update_id=20,
    )
    action, gap = mon.assess(diff, book_ready=True, book_last_update_id=10)
    assert action is DiffAction.RESNAPSHOT
    assert gap == 4


def test_assess_gap_on_prev_final_mismatch() -> None:
    mon = _monitor()
    diff = DepthDiff(
        symbol="BTCUSDT",
        bids=[],
        asks=[],
        first_update_id=12,
        final_update_id=20,
        prev_final_update_id=8,
    )
    action, gap = mon.assess(diff, book_ready=True, book_last_update_id=10)
    assert action is DiffAction.RESNAPSHOT
    assert gap == 1


def test_assess_apply_contiguous() -> None:
    mon = _monitor()
    diff = DepthDiff(
        symbol="BTCUSDT",
        bids=[],
        asks=[],
        first_update_id=11,
        final_update_id=50,
        prev_final_update_id=10,
    )
    action, gap = mon.assess(diff, book_ready=True, book_last_update_id=10)
    assert action is DiffAction.APPLY
    assert gap == 0


def test_on_snapshot_resets_gap_counter() -> None:
    mon = _monitor()
    mon.record_gap("BTCUSDT", 10)
    mon.on_snapshot("BTCUSDT", 100)
    assert mon.metrics()["BTCUSDT"]["sequence_gaps"] == 0


def test_record_gap_ignores_large_desync() -> None:
    mon = _monitor()
    mon.record_gap("BTCUSDT", 50_000)
    assert "BTCUSDT" not in mon.metrics()


def test_invalidate_marks_resnapshot() -> None:
    mon = _monitor()
    mon.on_snapshot("BTCUSDT", 100)
    mon.invalidate(["BTCUSDT"])
    assert mon.metrics()["BTCUSDT"]["needs_resnapshot"] is True
