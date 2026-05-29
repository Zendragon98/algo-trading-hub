"""breaker_enabled settings + LIVE disable confirmation."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BINANCE_API_KEY", "test")
os.environ.setdefault("BINANCE_API_SECRET", "test")

from common.breaker_registry import LIVE_DISABLE_CONFIRM_TOKEN, merge_breaker_enabled
from common.config import Settings
from common.enums import TradingMode


def test_default_breaker_enabled_has_all_codes() -> None:
    s = Settings(binance_api_key="x", binance_api_secret="y")
    assert s.is_breaker_enabled("stale_tick")
    assert s.is_breaker_enabled("operator_halt")
    assert len(s.breaker_enabled) >= 18


def test_merge_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="unknown breaker"):
        merge_breaker_enabled({"not_a_real_code": False})


def test_operator_halt_cannot_be_disabled() -> None:
    merged = merge_breaker_enabled({"operator_halt": False})
    assert merged["operator_halt"] is True


def test_md_crossed_book_syncs_from_legacy_flag() -> None:
    s = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        md_crossed_book_breaker=False,
    )
    assert s.is_breaker_enabled("md_crossed_book") is False


def test_assert_live_major_disable_requires_confirm() -> None:
    from api.breaker_patch import assert_live_major_disable_allowed

    settings = Settings(
        binance_api_key="x",
        binance_api_secret="y",
        trading_mode=TradingMode.LIVE,
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        assert_live_major_disable_allowed(
            settings,
            {"daily_loss": False},
            confirm_live_disable=False,
            confirm_token="",
        )
    assert exc.value.status_code == 400

    assert_live_major_disable_allowed(
        settings,
        {"daily_loss": False},
        confirm_live_disable=True,
        confirm_token=LIVE_DISABLE_CONFIRM_TOKEN,
    )
