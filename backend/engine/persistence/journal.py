"""Append-only WAL with monotonic sequence numbers, checkpoint, and replay."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from common.enums import EventType, OrderStatus, OrderType, Side
from common.events import Event
from common.types import ChildOrder, Fill, ParentOrder, Position

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class JournalCheckpoint:
    last_seq: int
    run_id: str
    started_at: str


@dataclass(slots=True)
class ReplaySummary:
    """Result of replaying a WAL into local trackers (hint before venue reconcile)."""

    events_read: int = 0
    fills_applied: int = 0
    orders_restored: int = 0
    positions_seeded: int = 0
    open_children: int = 0
    wal_path: str = ""
    errors: list[str] = field(default_factory=list)


class EventJournal:
    """Writes every bus event to ``events.wal.jsonl`` with seq/source."""

    def __init__(self, run_dir: Path, run_id: str, flush_every_sec: float = 1.0) -> None:
        self._run_dir = run_dir
        self._run_id = run_id
        self._flush_every_sec = flush_every_sec
        self._wal_path = run_dir / "events.wal.jsonl"
        self._meta_path = run_dir / "meta.json"
        self._handle: IO[str] | None = None
        self._last_seq = 0
        self._started_at = ""

    def open(self, started_at: str) -> None:
        self._started_at = started_at
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self._wal_path.open("a", encoding="utf-8")
        self._write_checkpoint()

    def append(self, event: Event) -> None:
        if self._handle is None:
            return
        self._last_seq = event.seq
        record = {
            "seq": event.seq,
            "source": event.source,
            "ts": event.ts,
            "type": event.type.value,
            "data": event.payload,
        }
        try:
            self._handle.write(json.dumps(record, default=str) + "\n")
        except (OSError, TypeError, ValueError):
            logger.exception("journal write failed for seq=%s", event.seq)

    def flush(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
            except OSError:
                logger.exception("journal flush failed")
        self._write_checkpoint()

    def close(self) -> None:
        self.flush()
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                logger.exception("journal close failed")
            self._handle = None

    def _write_checkpoint(self) -> None:
        meta = JournalCheckpoint(
            last_seq=self._last_seq,
            run_id=self._run_id,
            started_at=self._started_at,
        )
        try:
            self._meta_path.write_text(
                json.dumps({
                    "last_seq": meta.last_seq,
                    "run_id": meta.run_id,
                    "started_at": meta.started_at,
                }, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("journal checkpoint write failed")


def find_previous_wal(
    persist_base: Path,
    *,
    exclude_dir: Path | None = None,
) -> Path | None:
    """Return ``events.wal.jsonl`` from the most recent run folder before ``exclude_dir``."""
    if not persist_base.is_dir():
        return None
    candidates: list[Path] = []
    for child in persist_base.iterdir():
        if not child.is_dir():
            continue
        if exclude_dir is not None and child.resolve() == exclude_dir.resolve():
            continue
        wal = child / "events.wal.jsonl"
        if wal.is_file() and wal.stat().st_size > 0:
            candidates.append(wal)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_side(raw: str) -> Side:
    return Side(raw.lower()) if raw else Side.BUY


def _parse_order_status(raw: str) -> OrderStatus:
    return OrderStatus(raw.lower()) if raw else OrderStatus.NEW


def _parse_order_type(raw: str) -> OrderType:
    return OrderType(raw.upper()) if raw else OrderType.LIMIT


def _child_from_payload(data: dict[str, Any]) -> ChildOrder | None:
    cid = data.get("id") or data.get("client_order_id")
    if not cid:
        return None
    try:
        return ChildOrder(
            id=str(cid),
            parent_id=str(data.get("parent_id") or ""),
            symbol=str(data.get("symbol", "")),
            side=_parse_side(str(data.get("side", "buy"))),
            qty=float(data.get("qty") or 0),
            price=float(data["price"]) if data.get("price") is not None else None,
            order_type=_parse_order_type(str(data.get("order_type", "LIMIT"))),
            status=_parse_order_status(str(data.get("status", "new"))),
            filled_qty=float(data.get("filled_qty") or 0),
            avg_fill_price=float(data.get("avg_fill_price") or 0),
            venue_order_id=data.get("venue_order_id"),
            reduce_only=bool(data.get("reduce_only", False)),
        )
    except (TypeError, ValueError):
        return None


def _fill_from_payload(data: dict[str, Any]) -> Fill | None:
    cid = data.get("child_id") or data.get("id")
    if not cid:
        return None
    try:
        return Fill(
            child_id=str(cid),
            parent_id=data.get("parent_id"),
            symbol=str(data.get("symbol", "")),
            side=_parse_side(str(data.get("side", "buy"))),
            qty=float(data.get("qty") or 0),
            price=float(data.get("price") or data.get("venue_price") or 0),
            fee=float(data.get("fee") or 0),
            fee_asset=str(data.get("fee_asset") or "USDT"),
            trade_id=data.get("trade_id"),
            ts=float(data.get("ts") or 0),
            venue_price=float(data.get("venue_price") or data.get("price") or 0),
        )
    except (TypeError, ValueError):
        return None


def _position_from_payload(data: dict[str, Any]) -> Position | None:
    sym = data.get("symbol")
    if not sym:
        return None
    try:
        return Position(
            symbol=str(sym),
            qty=float(data.get("qty") or 0),
            entry_price=float(data.get("entry_price") or 0),
            mark_price=float(data.get("mark_price") or 0),
            unrealized_pnl=float(data.get("unrealized_pnl") or 0),
            realized_pnl=float(data.get("realized_pnl") or 0),
        )
    except (TypeError, ValueError):
        return None


async def replay_wal_async(
    wal_path: Path,
    oms: Any,
    positions: Any,
) -> ReplaySummary:
    """Rebuild OMS + positions from WAL (venue reconcile remains authoritative)."""
    summary = ReplaySummary(wal_path=str(wal_path))
    if not wal_path.is_file():
        summary.errors.append("wal_missing")
        return summary

    parents: dict[str, ParentOrder] = {}
    children: dict[str, ChildOrder] = {}
    position_list: list[Position] = []
    fills: list[Fill] = []

    try:
        lines = wal_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        summary.errors.append(f"read_failed:{exc}")
        return summary

    for line in lines:
        line = line.strip()
        if not line:
            continue
        summary.events_read += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            summary.errors.append(f"bad_json:line_{summary.events_read}")
            continue
        etype = record.get("type", "")
        data = record.get("data") or {}
        if etype == EventType.ORDER_UPDATE.value:
            child = _child_from_payload(data)
            if child is not None:
                children[child.id] = child
                summary.orders_restored += 1
                if child.parent_id and child.parent_id not in parents:
                    parents[child.parent_id] = ParentOrder(
                        id=child.parent_id,
                        symbol=child.symbol,
                        side=child.side,
                        qty=child.qty,
                    )
        elif etype == EventType.FILL.value:
            fill = _fill_from_payload(data)
            if fill is not None:
                fills.append(fill)
        elif etype == EventType.POSITION.value:
            pos = _position_from_payload(data)
            if pos is not None:
                position_list.append(pos)

    if position_list:
        positions.seed(position_list)
        summary.positions_seeded = len(position_list)

    for fill in fills:
        oms.restore_fill_seen(fill.trade_id)
        if fill.child_id in children:
            fill.parent_id = fill.parent_id or children[fill.child_id].parent_id
        await positions.on_fill(fill)
        summary.fills_applied += 1

    oms.restore_state(parents=parents, children=children)
    summary.open_children = len(list(oms.working_children()))
    logger.info(
        "WAL replay from %s: events=%d fills=%d orders=%d open_children=%d",
        wal_path.name,
        summary.events_read,
        summary.fills_applied,
        summary.orders_restored,
        summary.open_children,
    )
    return summary
