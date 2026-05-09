"""Pydantic DTOs that mirror the React console's TypeScript types.

The shape of every payload here is dictated by
`src/components/algo/mockData.ts` so the frontend can consume live data
without any client-side adaptation step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PositionDTO(BaseModel):
    symbol: str
    side: Literal["long", "short", "flat"]
    size: float
    entry: float
    mark: float


class TradeDTO(BaseModel):
    id: str
    ts: str            # HH:MM:SS in user locale, formatted server-side
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float
    pnl: float | None


class LogDTO(BaseModel):
    ts: str
    level: Literal["info", "warn", "error", "signal"]
    msg: str


class StatusDTO(BaseModel):
    status: Literal["running", "paused", "stopped"]
    uptime_sec: float
    paper_mode: bool = False    # reserved for future paper-trading toggle


class EquityDTO(BaseModel):
    equity: list[float]
    last_ts: float


class KpiDTO(BaseModel):
    equity: float
    open_pnl: float
    win_rate: float
    realized_pnl: float
    unrealized_pnl: float
    gross_notional: float
    net_notional: float


class ChildOrderDTO(BaseModel):
    """A single live or recent child order, for the OMS panel."""

    id: str
    parent_id: str | None
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    filled_qty: float
    price: float | None
    avg_fill_price: float
    order_type: Literal["limit", "market"]
    status: Literal["new", "ack", "partial", "filled", "cancelled", "rejected"]
    venue_order_id: str | None
    created_at: float
    updated_at: float


class ParentOrderDTO(BaseModel):
    """A working VWAP parent order with child progress + execution stats."""

    parent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    requested_qty: float
    filled_qty: float
    fill_ratio: float
    arrival_price: float
    vwap_price: float
    slippage_bps: float
    impact_bps: float
    duration_sec: float
    algo_mode: str | None
    started_at: float


class ExecutionReportDTO(BaseModel):
    """Completed parent order with full execution-quality breakdown."""

    parent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    requested_qty: float
    filled_qty: float
    fill_ratio: float
    arrival_price: float
    vwap_price: float
    slippage_bps: float
    impact_bps: float
    duration_sec: float
    algo_mode: str | None
    started_at: float
    completed_at: float | None


class ExecutionAggregateDTO(BaseModel):
    """Portfolio-wide execution-quality stats."""

    count: int
    avg_slippage_bps: float
    avg_impact_bps: float
    avg_fill_ratio: float
    avg_duration_sec: float
    total_traded_notional: float


class ExecutionStatsDTO(BaseModel):
    working: list[ParentOrderDTO]
    history: list[ExecutionReportDTO]
    aggregate: ExecutionAggregateDTO


class OrdersDTO(BaseModel):
    working: list[ChildOrderDTO]


class StateDTO(BaseModel):
    """Full snapshot used for the initial dashboard hydrate."""

    status: StatusDTO
    kpi: KpiDTO
    equity: EquityDTO
    positions: list[PositionDTO]
    trades: list[TradeDTO]
    orders: OrdersDTO
    execution: ExecutionStatsDTO


class RiskUpdateDTO(BaseModel):
    max_risk_pct: float
