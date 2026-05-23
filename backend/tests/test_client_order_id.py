"""Client order id uniqueness for MM and VWAP parents."""

from engine.orders.order_manager import new_client_order_id


def test_mm_quote_ids_unique_per_parent() -> None:
    a = new_client_order_id("Q-FILUSD-abcdef12", 0, prefix="MMQ")
    b = new_client_order_id("Q-FILUSD-fedcba98", 0, prefix="MMQ")
    assert a != b
    assert a == "MMQ-FILUSDabcdef12-00"
    assert b == "MMQ-FILUSDfedcba98-00"


def test_mm_quote_buy_sell_slices_differ() -> None:
    parent = "Q-CUSDT-81d13392"
    buy = new_client_order_id(parent, 0, prefix="MMQ")
    sell = new_client_order_id(parent, 1, prefix="MMQ")
    assert buy != sell
    assert buy.endswith("-00")
    assert sell.endswith("-01")


def test_vwap_parent_id_deterministic() -> None:
    parent = "P-abcdef1234"
    first = new_client_order_id(parent, 3)
    second = new_client_order_id(parent, 3)
    assert first == second
    assert len(first) <= 36


def test_flatten_parent_id() -> None:
    cid = new_client_order_id("P-flat-BTCUSDT", 0)
    assert "BTCUSDT" in cid
    assert len(cid) <= 36
