"""Flow momentum PnL — thin wrapper over canonical ``venue_pnl``."""

from __future__ import annotations

from ..position.venue_pnl import (
    VenuePnlSnapshot as FlowPnlSnapshot,
    apply_attributed_fill_vwap,
    compute_venue_pnl as compute_flow_pnl,
    maybe_log_pnl_verification as _maybe_log_pnl_verification,
    price_pnl_bps,
)


def maybe_log_pnl_verification(**kwargs):  # noqa: ANN003
    return _maybe_log_pnl_verification(tag="FLOW", **kwargs)


__all__ = [
    "FlowPnlSnapshot",
    "apply_attributed_fill_vwap",
    "compute_flow_pnl",
    "maybe_log_pnl_verification",
    "price_pnl_bps",
]
