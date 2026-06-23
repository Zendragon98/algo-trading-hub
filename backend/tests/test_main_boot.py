from __future__ import annotations

from argparse import Namespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from common.config import Settings
from main import _prepare_boot_settings, _should_autostart_engine


def test_no_engine_overrides_env_autostart() -> None:
    settings = Settings(engine_autostart=True)
    args = Namespace(engine=False, no_engine=True)

    assert not _should_autostart_engine(args, settings)


def test_engine_flag_overrides_stopped_default() -> None:
    settings = Settings(engine_autostart=False)
    args = Namespace(engine=True, no_engine=False)

    assert _should_autostart_engine(args, settings)


def test_default_boot_respects_engine_autostart_setting() -> None:
    args = Namespace(engine=False, no_engine=False)

    assert not _should_autostart_engine(args, Settings(engine_autostart=False))
    assert _should_autostart_engine(args, Settings(engine_autostart=True))


@pytest.mark.asyncio
async def test_boot_settings_resolve_auto_before_engine_start() -> None:
    settings = Settings(engine_autostart=False, sma_symbols=["AUTO"])
    expanded = settings.model_copy(update={"sma_symbols": ["BTCUSDT"]})
    partitioned = expanded.model_copy(update={"strategy": "sma"})

    with (
        patch("main.resolve_binance_auto_universe", new=AsyncMock(return_value=expanded)) as resolve,
        patch("main.partition_multi_strategy_universe", new=Mock(return_value=partitioned)) as partition,
    ):
        result = await _prepare_boot_settings(settings)

    resolve.assert_awaited_once_with(settings)
    partition.assert_called_once_with(expanded)
    assert result is partitioned
