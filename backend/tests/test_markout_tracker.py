"""MarkoutTracker adverse sign."""

from common.enums import Side
from common.types import Fill
from engine.market_data.markout_tracker import MarkoutTracker, _signed_markout_bps


def test_buy_adverse_when_mid_rises() -> None:
    bps = _signed_markout_bps(Side.BUY, 100.0, 101.0)
    assert bps > 0


def test_markout_ewma_updates() -> None:
    tr = MarkoutTracker(alpha=1.0)
    fill = Fill(
        child_id="c1",
        parent_id="p1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=1.0,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
    )
    tr.on_fill("BTCUSDT", fill, 100.0, 0.0)
    tr.on_mid("BTCUSDT", 101.0, 2.0)
    st = tr.stats("BTCUSDT")
    assert st.adverse_ewma_bps > 0
