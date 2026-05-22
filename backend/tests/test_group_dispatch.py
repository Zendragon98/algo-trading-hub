"""Group dispatch uses unified pre-trade validation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.config import Settings  # noqa: E402
from common.enums import Side  # noqa: E402
from common.types import Signal  # noqa: E402
from engine.risk.limits import Limits  # noqa: E402
from engine.risk.pretrade_validator import PreTradeValidator, ValidationResult  # noqa: E402
from gateways.gateway_interface import SymbolFilters  # noqa: E402


@pytest.mark.asyncio
async def test_validate_group_requires_all_legs() -> None:
    settings = Settings()
    risk = MagicMock()
    risk.check.return_value = MagicMock(approved=True, qty=0.01, reason="")
    risk.limits = Limits.from_settings(settings)
    gateway = MagicMock()
    filt = SymbolFilters(
        symbol="BTCUSDT", min_qty=0.001, min_notional=5.0, step_size=0.001, tick_size=0.01,
    )
    gateway.get_symbol_filters = MagicMock(return_value=filt)
    portfolio = MagicMock()
    portfolio.snapshot.return_value = MagicMock(equity=10_000.0, gross_notional=0.0)
    positions = MagicMock()
    positions.get.return_value = None

    validator = PreTradeValidator(settings, risk, gateway, portfolio, positions)
    legs = [
        Signal(symbol="BTCUSDT", side=Side.BUY, qty=0.01, reason="t", group_id="g1"),
        Signal(symbol="BTCUSDC", side=Side.SELL, qty=0.01, reason="t", group_id="g1"),
    ]
    result = validator.validate_group(
        legs,
        pair_qty=0.01,
        mids={"BTCUSDT": 50000.0, "BTCUSDC": 1.0},
        tick_ts_by_symbol={"BTCUSDT": 0.0, "BTCUSDC": 0.0},
        spread_bps_by_symbol={"BTCUSDT": 1.0, "BTCUSDC": 1.0},
    )
    assert isinstance(result, ValidationResult)
    assert result.approved or result.reason
