"""Journal WAL replay tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.enums import EventType, OrderStatus, Side  # noqa: E402
from common.events import EventBus  # noqa: E402
from common.types import ChildOrder, Fill, Position  # noqa: E402
from engine.orders.order_manager import OrderManager  # noqa: E402
from engine.persistence.journal import find_previous_wal, replay_wal_async  # noqa: E402
from engine.position.position_tracker import PositionTracker  # noqa: E402
from gateways.gateway_interface import GatewayInterface  # noqa: E402


class _Gw(GatewayInterface):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_market_data(self, *a, **kw) -> None: ...
    async def subscribe_user_data(self, *a, **kw) -> None: ...
    async def place_order(self, order): ...
    async def cancel_order(self, symbol: str, client_order_id: str) -> None: ...
    async def fetch_positions(self) -> list[Position]:
        return []
    async def fetch_balance(self) -> float:
        return 0.0
    async def book_snapshot(self, symbol: str, depth: int = 100) -> dict:
        return {"lastUpdateId": 0, "bids": [], "asks": []}
    async def klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        return []


@pytest.mark.asyncio
async def test_replay_wal_restores_child_and_position(tmp_path: Path) -> None:
    wal = tmp_path / "events.wal.jsonl"
    child = ChildOrder(
        id="ALPHA7-abc-01",
        parent_id="P-abc",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=50000.0,
        order_type=__import__("common.enums", fromlist=["OrderType"]).OrderType.LIMIT,
        status=OrderStatus.ACK,
    )
    fill = Fill(
        child_id=child.id,
        parent_id=child.parent_id,
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=0.01,
        price=50000.0,
        fee=0.1,
        fee_asset="USDT",
        trade_id="t1",
    )
    lines = [
        {"type": EventType.ORDER_UPDATE.value, "data": {
            "id": child.id, "parent_id": child.parent_id, "symbol": child.symbol,
            "side": "buy", "qty": child.qty, "price": child.price,
            "order_type": "LIMIT", "status": "ack", "filled_qty": 0, "avg_fill_price": 0,
        }},
        {"type": EventType.FILL.value, "data": {
            "child_id": fill.child_id, "parent_id": fill.parent_id, "symbol": fill.symbol,
            "side": "buy", "qty": fill.qty, "price": fill.price, "fee": fill.fee,
            "fee_asset": fill.fee_asset, "trade_id": fill.trade_id,
        }},
    ]
    wal.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")

    bus = EventBus()
    oms = OrderManager(gateway=_Gw(), bus=bus)
    positions = PositionTracker(bus=bus)
    summary = await replay_wal_async(wal, oms, positions)
    assert summary.orders_restored == 1
    assert summary.fills_applied == 1
    assert oms.child(child.id) is not None
    pos = positions.get("BTCUSDT")
    assert pos is not None
    assert pos.qty != 0


@pytest.mark.asyncio
async def test_replay_wal_skips_fill_deltas_when_positions_present(tmp_path: Path) -> None:
    wal = tmp_path / "events.wal.jsonl"
    lines = [
        {
            "type": EventType.POSITION.value,
            "data": {
                "symbol": "CRVUSDC",
                "qty": -5565.5,
                "avg_entry_price": 0.2591,
                "mark_price": 0.2591,
            },
        },
        {
            "type": EventType.FILL.value,
            "data": {
                "child_id": "ALPHA7-abc-01",
                "symbol": "CRVUSDC",
                "side": "sell",
                "qty": 5565.5,
                "price": 0.2591,
                "trade_id": "t1",
            },
        },
    ]
    wal.write_text("\n".join(json.dumps(r) for r in lines), encoding="utf-8")

    bus = EventBus()
    oms = OrderManager(gateway=_Gw(), bus=bus)
    positions = PositionTracker(bus=bus)
    summary = await replay_wal_async(wal, oms, positions)
    assert summary.positions_seeded == 1
    assert summary.fills_applied == 1
    pos = positions.get("CRVUSDC")
    assert pos is not None
    assert pos.qty == pytest.approx(-5565.5)


def test_find_previous_wal_excludes_current(tmp_path: Path) -> None:
    run_a = tmp_path / "2026-01-01T00-00-00Z"
    run_b = tmp_path / "2026-01-02T00-00-00Z"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "events.wal.jsonl").write_text('{"type":"tick","data":{}}\n', encoding="utf-8")
    (run_b / "events.wal.jsonl").write_text("", encoding="utf-8")
    found = find_previous_wal(tmp_path, exclude_dir=run_b)
    assert found == run_a / "events.wal.jsonl"
