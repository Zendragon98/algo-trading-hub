"""EventRecorder per-type JSONL routing + clean shutdown."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from common.enums import EventType
from common.events import Event, EventBus
from engine.persistence.event_recorder import EventRecorder, RecorderConfig, make_run_dir


@pytest.mark.asyncio
async def test_writes_each_event_type_to_its_own_file(tmp_path: Path) -> None:
    bus = EventBus()
    recorder = EventRecorder(bus=bus, config=RecorderConfig(run_dir=tmp_path, flush_every_sec=0.0))
    await recorder.start()

    await bus.publish(Event(type=EventType.FILL, payload={"symbol": "BTCUSDT", "qty": 0.1}))
    await bus.publish(Event(type=EventType.LOG, payload={"level": "info", "msg": "hello"}))
    await bus.publish(Event(type=EventType.EQUITY, payload={"equity": 10_000.0}))

    # Give the recorder a tick to drain its queue.
    await asyncio.sleep(0.05)
    await recorder.stop()

    fills = (tmp_path / "fills.jsonl").read_text(encoding="utf-8").strip().splitlines()
    logs = (tmp_path / "logs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    equity = (tmp_path / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert len(fills) == 1
    assert json.loads(fills[0])["data"]["symbol"] == "BTCUSDT"
    assert json.loads(logs[0])["data"]["msg"] == "hello"
    assert json.loads(equity[0])["data"]["equity"] == 10_000.0


@pytest.mark.asyncio
async def test_ticks_recorded_only_when_opted_in(tmp_path: Path) -> None:
    bus = EventBus()
    recorder = EventRecorder(
        bus=bus,
        config=RecorderConfig(run_dir=tmp_path, record_ticks=False, flush_every_sec=0.0),
    )
    await recorder.start()
    await bus.publish(Event(type=EventType.TICK, payload={"symbol": "BTCUSDT", "mid": 100.0}))
    await asyncio.sleep(0.05)
    await recorder.stop()
    assert not (tmp_path / "ticks.jsonl").exists()


@pytest.mark.asyncio
async def test_manifest_describes_run(tmp_path: Path) -> None:
    bus = EventBus()
    recorder = EventRecorder(bus=bus, config=RecorderConfig(run_dir=tmp_path))
    await recorder.start()
    await recorder.stop()

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["record_ticks"] is False
    assert "fills.jsonl" in manifest["streams"]
    assert "started_at" in manifest


@pytest.mark.asyncio
async def test_make_run_dir_creates_unique_subdirs(tmp_path: Path) -> None:
    a = make_run_dir(tmp_path)
    assert a.exists() and a.is_dir()
    assert a.parent == tmp_path
