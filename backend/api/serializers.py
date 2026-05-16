"""Engine-state -> DTO conversion helpers.

Centralised so the WebSocket layer and the REST handlers serialise the
same way. The fields and naming are dictated by the React console.
"""

from __future__ import annotations

from datetime import datetime, timezone

from common.types import ChildOrder, Position
from engine.core.engine import ALL_STRATEGIES_MODE, Engine
from engine.core.state import EngineSnapshot
from engine.execution.execution_metrics import ExecutionReport
from engine.performance.performance_tracker import TradeRecord
from engine.strategies.strategy_base import StrategyBase

from .schemas import (
    ChildOrderDTO,
    EquityDTO,
    ExecutionAggregateDTO,
    ExecutionReportDTO,
    ExecutionStatsDTO,
    KpiDTO,
    LogDTO,
    StrategyInfoDTO,
    OrdersDTO,
    ParentOrderDTO,
    PositionDTO,
    StateDTO,
    StatusDTO,
    SystemHealthDTO,
    TradeDTO,
)


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M:%S")


def position_to_dto(position: Position) -> PositionDTO:
    return PositionDTO(
        symbol=position.symbol,
        side=position.side.value,
        size=position.size,
        entry=position.avg_entry_price,
        mark=position.mark_price,
        unrealized_pnl=position.unrealized_pnl,
    )


def trade_to_dto(trade: TradeRecord) -> TradeDTO:
    return TradeDTO(
        id=trade.id,
        ts=_fmt_ts(trade.ts),
        symbol=trade.symbol,
        side=trade.side,  # type: ignore[arg-type]
        qty=trade.qty,
        price=trade.price,
        action=trade.action,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        pnl=trade.pnl,
    )


def child_order_to_dto(child: ChildOrder) -> ChildOrderDTO:
    return ChildOrderDTO(
        id=child.id,
        parent_id=child.parent_id,
        symbol=child.symbol,
        side=child.side.value,  # type: ignore[arg-type]
        qty=child.qty,
        filled_qty=child.filled_qty,
        price=child.price,
        avg_fill_price=child.avg_fill_price,
        # API / frontend use lowercase; OrderType keeps Binance uppercase.
        order_type=child.order_type.value.lower(),  # type: ignore[arg-type]
        status=child.status.value,  # type: ignore[arg-type]
        venue_order_id=child.venue_order_id,
        created_at=child.created_at,
        updated_at=child.updated_at,
    )


def execution_report_to_parent_dto(report: ExecutionReport) -> ParentOrderDTO:
    return ParentOrderDTO(
        parent_id=report.parent_id,
        symbol=report.symbol,
        side=report.side,  # type: ignore[arg-type]
        requested_qty=report.requested_qty,
        filled_qty=report.filled_qty,
        fill_ratio=report.fill_ratio,
        arrival_price=report.arrival_price,
        vwap_price=report.vwap_price,
        slippage_bps=report.slippage_bps,
        fee_adjusted_slippage_bps=report.fee_adjusted_slippage_bps,
        impact_bps=report.impact_bps,
        duration_sec=report.duration_sec,
        algo_mode=report.algo_mode,
        started_at=report.started_at,
    )


def execution_report_to_dto(report: ExecutionReport) -> ExecutionReportDTO:
    return ExecutionReportDTO(
        parent_id=report.parent_id,
        symbol=report.symbol,
        side=report.side,  # type: ignore[arg-type]
        requested_qty=report.requested_qty,
        filled_qty=report.filled_qty,
        fill_ratio=report.fill_ratio,
        arrival_price=report.arrival_price,
        vwap_price=report.vwap_price,
        slippage_bps=report.slippage_bps,
        fee_adjusted_slippage_bps=report.fee_adjusted_slippage_bps,
        impact_bps=report.impact_bps,
        duration_sec=report.duration_sec,
        algo_mode=report.algo_mode,
        started_at=report.started_at,
        completed_at=report.completed_at,
    )


def orders_dto(engine: Engine) -> OrdersDTO:
    return OrdersDTO(
        working=[child_order_to_dto(c) for c in engine.oms.working_children()],
    )


def execution_stats_dto(engine: Engine) -> ExecutionStatsDTO:
    tracker = engine.execution_tracker
    agg = tracker.aggregate()
    return ExecutionStatsDTO(
        working=[execution_report_to_parent_dto(r) for r in tracker.open_reports()],
        history=[execution_report_to_dto(r) for r in tracker.history()],
        aggregate=ExecutionAggregateDTO(
            count=int(agg["count"]),
            avg_slippage_bps=agg["avg_slippage_bps"],
            avg_impact_bps=agg["avg_impact_bps"],
            avg_fill_ratio=agg["avg_fill_ratio"],
            avg_duration_sec=agg["avg_duration_sec"],
            total_traded_notional=agg["total_traded_notional"],
        ),
    )


def strategy_to_dto(strategy: StrategyBase, *, active: bool = False) -> StrategyInfoDTO:
    # Fall back to the machine name when a strategy hasn't set its own
    # display copy yet — better than leaking an empty string to the UI.
    label = strategy.display_label or strategy.name
    return StrategyInfoDTO(
        name=strategy.name,
        label=label,
        description=strategy.description,
        active=active,
    )


def snapshot_to_state_dto(engine: Engine, snapshot: EngineSnapshot) -> StateDTO:
    open_pnl = sum(p.unrealized_pnl for p in snapshot.positions)
    active_name = engine.active_strategy_name
    multi = engine.is_multi_strategy_mode()
    strategies = [
        strategy_to_dto(s, active=multi or s.name == active_name) for s in engine.strategies
    ]
    # ``StateDTO.strategy`` is the *active* one (not the first registered)
    # so legacy frontends that only read this field still see what the
    # engine is actually running after a hot-swap.
    if multi:
        active_dto = StrategyInfoDTO(
            name=ALL_STRATEGIES_MODE,
            label="All strategies (netted)",
            description="Runs pairs, SMA, and market making with internal position netting.",
            active=True,
        )
    else:
        active_dto = next(
            (dto for dto in strategies if dto.active),
            strategies[0] if strategies else None,
        )
    return StateDTO(
        status=StatusDTO(
            status=snapshot.status.value,
            uptime_sec=snapshot.uptime_sec,
            paper_mode=not engine.settings.is_live,
        ),
        strategy=active_dto,
        strategies=strategies,
        kpi=KpiDTO(
            equity=snapshot.equity,
            open_pnl=open_pnl,
            win_rate=snapshot.win_rate,
            gross_win_pnl=snapshot.gross_win_pnl,
            gross_loss_pnl=snapshot.gross_loss_pnl,
            profit_factor=snapshot.profit_factor,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            gross_notional=snapshot.gross_notional,
            net_notional=snapshot.net_notional,
            win_rate_session=snapshot.win_rate_session,
            gross_win_pnl_session=snapshot.gross_win_pnl_session,
            gross_loss_pnl_session=snapshot.gross_loss_pnl_session,
            profit_factor_session=snapshot.profit_factor_session,
            session_close_wins=snapshot.session_close_wins,
            session_close_losses=snapshot.session_close_losses,
            session_close_breakevens=snapshot.session_close_breakevens,
        ),
        equity=EquityDTO(equity=snapshot.equity_curve, last_ts=snapshot.last_tick_ts),
        positions=[position_to_dto(p) for p in snapshot.positions],
        trades=[trade_to_dto(t) for t in snapshot.trades],
        realized_trades=[trade_to_dto(t) for t in snapshot.realized_trades],
        orders=orders_dto(engine),
        execution=execution_stats_dto(engine),
        system_health=SystemHealthDTO(**engine.system_health()),
        event_archive_run_dir=str(engine.event_archive_dir.resolve())
        if engine.event_archive_dir is not None
        else None,
    )


def log_event_to_dto(payload: dict) -> LogDTO:
    return LogDTO(
        ts=_fmt_ts(payload.get("ts", 0.0)) if "ts" in payload else _fmt_now(),
        level=payload.get("level", "info"),  # type: ignore[arg-type]
        msg=payload.get("msg", ""),
    )


def _fmt_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
