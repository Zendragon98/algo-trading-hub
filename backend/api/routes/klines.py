"""GET /api/klines.

Thin pass-through to the active gateway's `klines()` method so the
dashboard can render real OHLCV history (e.g. for the position chart).
The engine never persists candles itself; we proxy the venue REST call
on demand and convert each row into the wire `KlineDTO` once.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from common.config import get_settings
from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import KlineDTO

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["klines"])

# Same key within TTL seconds → return cached DTOs (reduces duplicate REST klines load).
_klines_cache: dict[tuple[str, str, int], tuple[float, list[KlineDTO]]] = {}

# Binance accepts "1m", "3m", "5m", ..., "1d"; we lock the API to the
# subset the dashboard actually shows so a typo can't slip through.
_ALLOWED_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}


@router.get("/klines", response_model=list[KlineDTO])
async def klines(
    symbol: str = Query(..., min_length=1, max_length=20),
    interval: str = Query("15m"),
    limit: int = Query(120, ge=1, le=500),
    engine: Engine = Depends(get_engine),
) -> list[KlineDTO]:
    if interval not in _ALLOWED_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"interval must be one of {sorted(_ALLOWED_INTERVALS)}",
        )
    ttl = max(0.0, float(get_settings().klines_cache_ttl_sec))
    key = (symbol.strip().upper(), interval, limit)
    now = time.monotonic()
    if ttl > 0 and key in _klines_cache:
        ts, cached = _klines_cache[key]
        if now - ts < ttl:
            return cached
    try:
        bars = await engine.gateway.klines(symbol=symbol, interval=interval, limit=limit)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("klines fetch failed for %s %s", symbol, interval)
        raise HTTPException(status_code=502, detail=f"upstream klines error: {exc}") from exc
    out = [
        KlineDTO(
            open_time=bar.open_time,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            close_time=bar.close_time,
        )
        for bar in bars
    ]
    if ttl > 0:
        _klines_cache[key] = (now, out)
    return out
