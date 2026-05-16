"""User-data health payload matches ConnectionMonitor + reconcile rules."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from common.enums import EngineStatus
from common.events import EventBus
from engine.core.engine import Engine
from engine.orders.order_manager import OrderManager


def _engine_stub(
    *,
    running: bool,
    last_ws_user_activity_ts: float,
    last_venue_truth_ts: float,
    working_orders: bool,
    gross_notional: float = 0.0,
) -> Engine:
    eng = Engine.__new__(Engine)
    eng._state = MagicMock(status=EngineStatus.RUNNING if running else EngineStatus.PAUSED)
    eng._oms = MagicMock()
    eng._oms.last_ws_user_activity_ts = last_ws_user_activity_ts
    eng._oms.last_venue_truth_ts = last_venue_truth_ts
    eng._oms.working_children.return_value = iter([MagicMock()]) if working_orders else iter(())
    eng._settings = MagicMock(
        ws_stale_pause_sec=30.0,
        reconcile_user_data_fresh_sec=120.0,
    )
    snap = MagicMock(gross_notional=gross_notional)
    eng.snapshot = MagicMock(return_value=snap)
    return eng


def test_user_data_stale_only_when_monitored_and_ws_old() -> None:
    now = time.time()
    eng = _engine_stub(
        running=True,
        last_ws_user_activity_ts=now - 45.0,
        last_venue_truth_ts=now - 45.0,
        working_orders=True,
    )
    health = eng._user_data_health(now)
    assert health["user_data_monitored"] is True
    assert health["user_data_stale"] is True
    assert health["user_ws_event_age_sec"] > 40.0


def test_idle_account_not_stale_despite_high_ws_age() -> None:
    now = time.time()
    eng = _engine_stub(
        running=True,
        last_ws_user_activity_ts=now - 300.0,
        last_venue_truth_ts=now - 300.0,
        working_orders=False,
    )
    health = eng._user_data_health(now)
    assert health["user_data_monitored"] is False
    assert health["user_data_stale"] is False


def test_exposure_truth_stale_flags_reconcile_stale() -> None:
    now = time.time()
    eng = _engine_stub(
        running=True,
        last_ws_user_activity_ts=now - 200.0,
        last_venue_truth_ts=now - 200.0,
        working_orders=False,
        gross_notional=25_000.0,
    )
    health = eng._user_data_health(now)
    assert health["user_data_monitored"] is False
    assert health["user_data_stale"] is False
    assert health["user_data_reconcile_stale"] is True


def test_exposure_ws_idle_but_recent_rest_not_reconcile_stale() -> None:
    """Holding exposure with a quiet user stream is normal; REST keeps truth fresh."""
    now = time.time()
    eng = _engine_stub(
        running=True,
        last_ws_user_activity_ts=now - 800.0,
        last_venue_truth_ts=now - 30.0,
        working_orders=False,
        gross_notional=25_000.0,
    )
    health = eng._user_data_health(now)
    assert health["user_data_reconcile_stale"] is False
    assert health["user_data_age_sec"] < 60.0
    assert health["user_ws_event_age_sec"] > 700.0


def test_touch_methods_on_order_manager() -> None:
    bus = EventBus()
    oms = OrderManager(MagicMock(), bus)
    assert oms.last_ws_user_activity_ts == 0.0
    assert oms.last_venue_truth_ts == 0.0
    oms.touch_ws_user_data_activity()
    assert oms.last_ws_user_activity_ts > 0.0
    assert oms.last_venue_truth_ts == oms.last_ws_user_activity_ts
    oms.touch_venue_truth_from_rest()
    assert oms.last_venue_truth_ts >= oms.last_ws_user_activity_ts
