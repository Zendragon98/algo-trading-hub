"""Clock skew surfaced from gateway time offset."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.core.engine import Engine


@pytest.mark.asyncio
async def test_refresh_clock_skew_reads_gateway() -> None:
    eng = Engine.__new__(Engine)
    eng._gateway = MagicMock()
    eng._gateway.sync_clock = AsyncMock()
    eng._gateway.clock_skew_ms.return_value = 42.0
    eng._clock_skew_ms = 0.0
    eng._clock_skew_synced = False

    await Engine._refresh_clock_skew(eng)

    eng._gateway.sync_clock.assert_awaited_once()
    assert eng._clock_skew_ms == 42.0
    assert eng._clock_skew_synced is True


@pytest.mark.asyncio
async def test_refresh_clock_skew_keeps_last_value_on_failure() -> None:
    eng = Engine.__new__(Engine)
    eng._gateway = MagicMock()
    eng._gateway.sync_clock = AsyncMock(side_effect=RuntimeError("network"))
    eng._clock_skew_ms = 15.0
    eng._clock_skew_synced = True

    await Engine._refresh_clock_skew(eng)

    assert eng._clock_skew_ms == 15.0
    assert eng._clock_skew_synced is True
