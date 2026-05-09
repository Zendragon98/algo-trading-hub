"""GET /api/orders.

Returns the working set of child orders the OMS is tracking, in the
same shape the React OMS panel expects. Filled / cancelled orders
intentionally fall off so the panel stays focused on what's actionable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.core.engine import Engine

from ..dependencies import get_engine
from ..schemas import OrdersDTO
from ..serializers import orders_dto

router = APIRouter(prefix="/api", tags=["orders"])


@router.get("/orders", response_model=OrdersDTO)
def orders(engine: Engine = Depends(get_engine)) -> OrdersDTO:
    return orders_dto(engine)
