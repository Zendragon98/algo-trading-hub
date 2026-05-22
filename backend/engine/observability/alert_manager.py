"""Critical-event alerts — webhook + bus LOG mirror."""

from __future__ import annotations

import logging
import time as _time
from typing import Any

import httpx

from common.enums import EventType, LogLevel
from common.events import Event, EventBus

logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(
        self,
        bus: EventBus,
        *,
        webhook_url: str = "",
        cooldown_sec: float = 60.0,
    ) -> None:
        self._bus = bus
        self._webhook_url = (webhook_url or "").strip()
        self._cooldown_sec = max(1.0, cooldown_sec)
        self._last_sent: dict[str, float] = {}

    async def fire(self, key: str, message: str, *, extra: dict[str, Any] | None = None) -> None:
        now = _time.time()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._cooldown_sec:
            return
        self._last_sent[key] = now

        payload = {"key": key, "message": message, **(extra or {})}
        await self._bus.publish(
            Event(
                type=EventType.LOG,
                payload={"level": LogLevel.ERROR.value, "msg": f"ALERT: {message}"},
                source="alert_manager",
            )
        )
        if not self._webhook_url:
            return
        body = {"text": message, **payload}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(self._webhook_url, json=body)
        except Exception:  # noqa: BLE001
            logger.exception("alert webhook failed for key=%s", key)
