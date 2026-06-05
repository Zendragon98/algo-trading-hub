"""Unified pre-trade validation for single-leg and grouped signals."""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field

from common.config import Settings
from common.enums import Side
from common.logging import group_signal_log, signal_log_emit
from common.types import Signal
from gateways.gateway_interface import GatewayInterface

from ..portfolio.portfolio import Portfolio
from ..position.position_tracker import PositionTracker
from .risk_manager import RiskManager
from .venue_sizing import venue_cap_qty, venue_min_qty

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidationResult:
    approved: bool
    qty: float = 0.0
    reason: str = ""
    vetoes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _DedupEntry:
    expires_at: float


class PreTradeValidator:
    """Single entry for pre-router checks on singles and pair groups."""

    def __init__(
        self,
        settings: Settings,
        risk: RiskManager,
        gateway: GatewayInterface,
        portfolio: Portfolio,
        positions: PositionTracker,
        *,
        venue_qty_for: Callable[[str], float | None] | None = None,
        on_ledger_venue_flat_heal: Callable[[str, str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._risk = risk
        self._gateway = gateway
        self._portfolio = portfolio
        self._positions = positions
        self._venue_qty_for = venue_qty_for
        self._on_ledger_venue_flat_heal = on_ledger_venue_flat_heal
        self._dedup: dict[tuple[str, ...], _DedupEntry] = {}

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings

    def validate_single(
        self,
        signal: Signal,
        mid: float,
        *,
        tick_ts: float | None,
        spread_bps: float | None,
        skip_dedup: bool = False,
    ) -> ValidationResult:
        vetoes: list[str] = []
        if signal.reduce_only:
            return self._validate_reduce_only(signal, mid, skip_dedup=skip_dedup)

        if not skip_dedup and self._is_duplicate(signal):
            return ValidationResult(False, reason="signal_dedup", vetoes=["signal_dedup"])

        decision = self._risk.check(
            signal, mid, tick_ts=tick_ts, spread_bps=spread_bps,
        )
        if not decision.approved:
            vetoes.append(decision.reason or "risk_veto")
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: {decision.reason}",
                reason=signal.reason,
            )
            return ValidationResult(False, reason=decision.reason, vetoes=vetoes)

        qty = decision.qty
        if qty + 1e-12 < signal.qty:
            signal_log_emit(
                logger,
                f"risk scaled {signal.symbol} {signal.qty:.4f} -> {qty:.4f}",
                reason=signal.reason,
            )
        ff = self._fat_finger(signal.symbol, qty, mid)
        if ff is not None:
            vetoes.append(ff)
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: {ff}",
                reason=signal.reason,
            )
            return ValidationResult(False, reason=ff, vetoes=vetoes)

        filters = self._gateway.get_symbol_filters(signal.symbol)
        floor = venue_min_qty(mid=mid, filters=filters)
        if floor is None:
            vetoes.append("venue_veto")
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: venue_veto",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="venue_veto", vetoes=vetoes)

        final_qty = max(qty, floor)
        capped = venue_cap_qty(final_qty, filters)
        if capped + 1e-12 < floor:
            vetoes.append("venue_max_qty")
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: venue_max_qty",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="venue_max_qty", vetoes=vetoes)
        if capped + 1e-12 < final_qty:
            signal_log_emit(
                logger,
                f"venue capped {signal.symbol} {final_qty:.4f} -> {capped:.4f}",
                reason=signal.reason,
            )
            final_qty = capped
        if final_qty > qty + 1e-12:
            bump = self._venue_bump_ok(signal.symbol, final_qty, mid)
            if not bump.approved:
                vetoes.extend(bump.vetoes)
                signal_log_emit(
                    logger,
                    f"pretrade veto {signal.symbol}: {bump.reason or 'venue_bump'}",
                    reason=signal.reason,
                )
                return bump

        if not skip_dedup:
            self._record_dedup(signal, final_qty)
        return ValidationResult(True, qty=final_qty, vetoes=vetoes)

    def validate_group(
        self,
        legs: list[Signal],
        pair_qty: float,
        mids: dict[str, float],
        *,
        tick_ts_by_symbol: dict[str, float | None],
        spread_bps_by_symbol: dict[str, float | None],
    ) -> ValidationResult:
        vetoes: list[str] = []
        if pair_qty <= 0:
            if legs:
                group_signal_log(logger, legs[0].group_id or "group", "pretrade veto: zero_qty", legs)
            return ValidationResult(False, reason="zero_qty", vetoes=["zero_qty"])

        for leg in legs:
            mid = mids.get(leg.symbol)
            if mid is None or mid <= 0:
                vetoes.append(f"no_mid:{leg.symbol}")
                group_signal_log(
                    logger,
                    legs[0].group_id or "group",
                    f"pretrade veto: no_mid:{leg.symbol}",
                    legs,
                )
                return ValidationResult(False, reason=f"no_mid:{leg.symbol}", vetoes=vetoes)

        for leg in legs:
            leg_signal = Signal(
                symbol=leg.symbol,
                side=leg.side,
                qty=pair_qty,
                reason=leg.reason,
                score=leg.score,
                group_id=leg.group_id,
            )
            result = self.validate_single(
                leg_signal,
                mids[leg.symbol],
                tick_ts=tick_ts_by_symbol.get(leg.symbol),
                spread_bps=spread_bps_by_symbol.get(leg.symbol),
                skip_dedup=True,
            )
            if not result.approved:
                vetoes.extend(result.vetoes)
                group_signal_log(
                    logger,
                    leg.group_id or "group",
                    f"pretrade veto: {leg.symbol}:{result.reason}",
                    legs,
                )
                return ValidationResult(
                    False,
                    reason=f"group_leg:{leg.symbol}:{result.reason}",
                    vetoes=vetoes,
                )
            if result.qty + 1e-12 < pair_qty:
                vetoes.append(f"risk_scale:{leg.symbol}")
                group_signal_log(
                    logger,
                    leg.group_id or "group",
                    f"pretrade veto: risk_scale:{leg.symbol}",
                    legs,
                )
                return ValidationResult(
                    False,
                    reason=f"risk_scale:{leg.symbol}",
                    vetoes=vetoes,
                )

        for leg in legs:
            self._record_dedup(leg, pair_qty)
        return ValidationResult(True, qty=pair_qty, vetoes=vetoes)

    def check_limit_collar(
        self,
        symbol: str,
        limit_price: float,
        mid: float,
    ) -> tuple[bool, str]:
        """Return (ok, reason) for a LIMIT peg vs mid."""
        cap_bps = float(self._settings.max_limit_deviation_bps)
        if cap_bps <= 0 or mid <= 0:
            return True, ""
        dev_bps = abs(limit_price - mid) / mid * 10_000.0
        if dev_bps > cap_bps:
            return False, f"limit_collar:{dev_bps:.1f}bps>{cap_bps:.1f}bps"
        return True, ""

    def _validate_reduce_only(
        self,
        signal: Signal,
        mid: float,
        *,
        skip_dedup: bool,
    ) -> ValidationResult:
        """Light path for strategy-driven position reductions."""
        tol = 1e-9
        venue_q = self._venue_qty_for(signal.symbol) if self._venue_qty_for else None
        if venue_q is not None:
            if abs(venue_q) <= tol:
                sn = (signal.strategy_name or "").strip()
                if self._on_ledger_venue_flat_heal and sn and sn != "__netted__":
                    self._on_ledger_venue_flat_heal(sn, signal.symbol)
                signal_log_emit(
                    logger,
                    f"pretrade veto {signal.symbol}: reduce_only_venue_flat",
                    reason=signal.reason,
                )
                return ValidationResult(False, reason="reduce_only_venue_flat")
            venue_close = Side.SELL if venue_q > 0 else Side.BUY
            if signal.side is not venue_close:
                signal_log_emit(
                    logger,
                    f"pretrade veto {signal.symbol}: reduce_only_venue_wrong_side",
                    reason=signal.reason,
                )
                return ValidationResult(False, reason="reduce_only_venue_wrong_side")

        pos = self._positions.get(signal.symbol)
        if pos is None or abs(pos.qty) <= 0:
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: reduce_only_no_position",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="reduce_only_no_position")

        close_side = Side.SELL if pos.qty > 0 else Side.BUY
        if signal.side is not close_side:
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: reduce_only_wrong_side",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="reduce_only_wrong_side")

        close_qty = min(signal.qty, abs(pos.qty))
        if venue_q is not None:
            close_qty = min(close_qty, abs(venue_q))
        if close_qty <= 0:
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: reduce_only_zero_qty",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="reduce_only_zero_qty")

        if not skip_dedup and self._is_duplicate(signal):
            return ValidationResult(False, reason="signal_dedup", vetoes=["signal_dedup"])

        filters = self._gateway.get_symbol_filters(signal.symbol)
        floor = venue_min_qty(mid=mid, filters=filters)
        if floor is None:
            signal_log_emit(
                logger,
                f"pretrade veto {signal.symbol}: venue_veto",
                reason=signal.reason,
            )
            return ValidationResult(False, reason="venue_veto", vetoes=["venue_veto"])

        final_qty = max(close_qty, floor) if close_qty < abs(pos.qty) else close_qty
        if final_qty > abs(pos.qty) + 1e-12:
            final_qty = abs(pos.qty)

        if not skip_dedup:
            self._record_dedup(signal, final_qty)
        return ValidationResult(True, qty=final_qty)

    def _fat_finger(self, symbol: str, qty: float, mid: float) -> str | None:
        notional = qty * mid
        cap_usd = float(self._settings.max_order_notional_usd)
        if cap_usd > 0 and notional > cap_usd:
            return "fat_finger_notional_usd"

        mult = float(self._settings.max_qty_vs_position_multiple)
        if mult > 0:
            pos = self._positions.get(symbol)
            if pos is not None and abs(pos.qty) > 0:
                if qty > abs(pos.qty) * mult + 1e-12:
                    return "fat_finger_qty_vs_position"
        return None

    def _venue_bump_ok(self, symbol: str, final_qty: float, mid: float) -> ValidationResult:
        snap = self._portfolio.snapshot()
        max_notional = snap.equity * self._risk.limits.max_risk_pct
        required_notional = final_qty * mid
        projected_gross = snap.gross_notional + required_notional
        vetoes: list[str] = []
        if required_notional > max_notional:
            vetoes.append("venue_bump_risk_cap")
            return ValidationResult(False, reason="venue_bump_risk_cap", vetoes=vetoes)
        if projected_gross > self._risk.limits.max_gross_notional:
            vetoes.append("venue_bump_gross")
            return ValidationResult(False, reason="venue_bump_gross", vetoes=vetoes)
        return ValidationResult(True, qty=final_qty)

    def _dedup_key(self, signal: Signal, qty: float) -> tuple[str, ...]:
        rounded = f"{qty:.8f}"
        if signal.group_id:
            return ("group", signal.group_id, signal.reason, rounded)
        return (signal.symbol, signal.side.value, signal.reason, rounded)

    def _is_duplicate(self, signal: Signal) -> bool:
        ttl = float(self._settings.signal_dedup_ttl_sec)
        if ttl <= 0:
            return False
        now = _time.time()
        self._prune_dedup(now)
        key = self._dedup_key(signal, signal.qty)
        entry = self._dedup.get(key)
        if entry is not None and entry.expires_at > now:
            signal_log_emit(
                logger,
                f"signal dedup veto {signal.symbol}",
                reason=signal.reason,
            )
            return True
        return False

    def _record_dedup(self, signal: Signal, qty: float) -> None:
        ttl = float(self._settings.signal_dedup_ttl_sec)
        if ttl <= 0:
            return
        key = self._dedup_key(signal, qty)
        self._dedup[key] = _DedupEntry(expires_at=_time.time() + ttl)

    def _prune_dedup(self, now: float) -> None:
        stale = [k for k, v in self._dedup.items() if v.expires_at <= now]
        for k in stale:
            self._dedup.pop(k, None)
