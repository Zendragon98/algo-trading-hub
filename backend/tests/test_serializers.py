"""API serializer contract tests."""

from __future__ import annotations

from common.enums import OrderStatus, OrderType, Side
from common.types import ChildOrder

from api.serializers import child_order_to_dto


def test_child_order_to_dto_normalizes_order_type_for_api() -> None:
    child = ChildOrder(
        id="c-1",
        parent_id="p-1",
        symbol="ETHUSDC",
        side=Side.BUY,
        qty=1.0,
        price=None,
        order_type=OrderType.MARKET,
        status=OrderStatus.ACK,
    )
    dto = child_order_to_dto(child)
    assert dto.order_type == "market"
