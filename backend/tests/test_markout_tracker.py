"""MarkoutTracker adverse sign, horizons, and listener."""

from common.enums import Side
from common.types import Fill
from engine.market_data.markout_tracker import (
    MarkoutObservation,
    MarkoutTracker,
    _signed_markout_bps,
)


def _buy_fill() -> Fill:
    return Fill(
        child_id="c1",
        parent_id="p1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=1.0,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
    )


def test_buy_adverse_when_mid_rises() -> None:
    bps = _signed_markout_bps(Side.BUY, 100.0, 101.0)
    assert bps > 0


def test_markout_ewma_updates() -> None:
    tr = MarkoutTracker(alpha=1.0, horizons_sec=(1.0,))
    tr.on_fill("BTCUSDT", _buy_fill(), 100.0, 0.0, strategy_name="market_making_v2")
    tr.on_mid("BTCUSDT", 101.0, 2.0)
    st = tr.stats("BTCUSDT")
    assert st.adverse_ewma_bps > 0


def test_multi_horizon_emits_once_per_horizon() -> None:
    seen: list[float] = []

    def listener(obs: MarkoutObservation) -> None:
        seen.append(obs.horizon_sec)

    tr = MarkoutTracker(alpha=0.15, horizons_sec=(1.0, 5.0, 30.0), listener=listener)
    tr.on_fill("BTCUSDT", _buy_fill(), 100.0, 0.0)
    tr.on_mid("BTCUSDT", 100.5, 0.5)  # before 1s
    assert seen == []
    tr.on_mid("BTCUSDT", 101.0, 1.5)  # 1s horizon
    assert seen == [1.0]
    tr.on_mid("BTCUSDT", 101.0, 6.0)  # 5s horizon
    assert seen == [1.0, 5.0]
    tr.on_mid("BTCUSDT", 101.0, 31.0)  # 30s horizon
    assert seen == [1.0, 5.0, 30.0]
