"""Pipeline latency histograms (tick → signal → risk → submit → ack)."""

from __future__ import annotations

import logging
import time as _time
from collections import defaultdict
from dataclasses import dataclass

from common.enums import EventType
from common.events import Event, EventBus

logger = logging.getLogger(__name__)

_MAX_SAMPLES = 500


@dataclass(slots=True)
class _Span:
    symbol: str
    tick_received: float = 0.0
    signal_emitted: float = 0.0
    risk_passed: float = 0.0
    child_submitted: float = 0.0
    venue_ack: float = 0.0


class LatencyTracker:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._active: dict[str, _Span] = {}
        self._deltas_ms: dict[str, list[float]] = defaultdict(list)
        self._last_emit = 0.0

    def on_tick(self, symbol: str) -> None:
        self._active[symbol] = _Span(symbol=symbol, tick_received=_time.time())

    def on_signal(self, symbol: str) -> None:
        span = self._active.setdefault(symbol, _Span(symbol=symbol))
        span.signal_emitted = _time.time()

    def on_risk_passed(self, symbol: str) -> None:
        span = self._active.setdefault(symbol, _Span(symbol=symbol))
        span.risk_passed = _time.time()

    def on_child_submitted(self, symbol: str) -> None:
        span = self._active.setdefault(symbol, _Span(symbol=symbol))
        span.child_submitted = _time.time()

    def on_venue_ack(self, symbol: str) -> None:
        span = self._active.get(symbol)
        if span is None:
            return
        span.venue_ack = _time.time()
        if span.tick_received > 0 and span.signal_emitted > 0:
            self._record(
                "tick_to_signal_ms",
                (span.signal_emitted - span.tick_received) * 1000.0,
            )
        if span.signal_emitted > 0 and span.risk_passed > 0:
            self._record(
                "signal_to_risk_ms",
                (span.risk_passed - span.signal_emitted) * 1000.0,
            )
        if span.risk_passed > 0 and span.child_submitted > 0:
            self._record(
                "risk_to_submit_ms",
                (span.child_submitted - span.risk_passed) * 1000.0,
            )
        if span.tick_received > 0 and span.child_submitted > 0:
            self._record(
                "tick_to_submit_ms",
                (span.child_submitted - span.tick_received) * 1000.0,
            )
        if span.child_submitted > 0 and span.venue_ack > 0:
            self._record(
                "submit_to_ack_ms",
                (span.venue_ack - span.child_submitted) * 1000.0,
            )
        if span.tick_received > 0 and span.venue_ack > 0:
            self._record(
                "tick_to_ack_ms",
                (span.venue_ack - span.tick_received) * 1000.0,
            )
        self._active.pop(symbol, None)

    def histograms(self) -> dict[str, dict[str, float]]:
        """Current percentile snapshot without publishing."""
        payload: dict[str, dict[str, float]] = {}
        for key, samples in self._deltas_ms.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            payload[key] = {
                "p50": sorted_s[n // 2],
                "p95": sorted_s[int(n * 0.95)] if n > 1 else sorted_s[-1],
                "p99": sorted_s[int(n * 0.99)] if n > 1 else sorted_s[-1],
                "count": float(n),
            }
        return payload

    async def maybe_emit(self, interval_sec: float) -> None:
        now = _time.time()
        if now - self._last_emit < interval_sec:
            return
        self._last_emit = now
        payload: dict[str, object] = {"kind": "latency_metrics"}
        for key, samples in self._deltas_ms.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            payload[key] = {
                "p50": sorted_s[n // 2],
                "p95": sorted_s[int(n * 0.95)] if n > 1 else sorted_s[-1],
                "p99": sorted_s[int(n * 0.99)] if n > 1 else sorted_s[-1],
                "count": n,
            }
        if len(payload) <= 1:
            return
        summary = ", ".join(
            f"{k}:p95={v['p95']:.0f}ms"
            for k, v in payload.items()
            if k != "kind" and isinstance(v, dict) and "p95" in v
        )
        if summary:
            logger.debug("latency_metrics %s", summary)
        await self._bus.publish(Event(type=EventType.STATUS, payload=payload))

    def _record(self, key: str, delta_ms: float) -> None:
        bucket = self._deltas_ms[key]
        bucket.append(delta_ms)
        if len(bucket) > _MAX_SAMPLES:
            self._deltas_ms[key] = bucket[-_MAX_SAMPLES:]
