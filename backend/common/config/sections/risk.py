from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from common.breaker_registry import default_breaker_enabled_map, merge_breaker_enabled

class RiskMixin(BaseModel):
    # --- Risk ---
    # `max_risk_pct` is the hard ceiling on per-leg notional (% of equity).
    # `risk_per_trade_pct` is the *primary* sizing input: the fraction of
    # equity the engine is willing to lose if `default_stop_loss_pct` is
    # hit. Position size is then `(equity * risk_per_trade_pct) / stop_pct`,
    # which is the canonical "size by stop" rule traders use on futures.
    # Paper OMS stress: 12% risk / 2% stop => 6× equity target notional before caps;
    # max_risk_pct and max_symbol_notional_pct at 1.0 allow up to 1× equity per leg.
    max_risk_pct: float = 1.0
    risk_per_trade_pct: float = 0.003
    max_gross_notional: float = 500_000.0
    max_drawdown_pct: float = 0.12
    default_stop_loss_pct: float = 0.02
    default_take_profit_pct: float = 0.06

    # --- Failsafes (circuit breakers) ---
    # Pre-trade gates
    max_tick_age_sec: float = 5.0           # stale-tick veto threshold
    max_entry_spread_bps: float = 25.0      # wide-spread veto when spread_dynamic_enabled is False
    spread_dynamic_enabled: bool = Field(
        default=True,
        description="Per-symbol EWMA spread gate; set False to use max_entry_spread_bps only.",
    )
    spread_baseline_alpha: float = Field(
        default=0.06,
        description="EWMA weight on each new quoted spread sample (MarketDataGuard).",
    )
    spread_wide_multiplier: float = Field(
        default=2.5,
        description="Veto entry when spread > this × baseline EWMA (before floor/ceiling clamp).",
    )
    spread_wide_floor_bps: float = Field(
        default=8.0,
        description="Minimum dynamic spread allowance in bps.",
    )
    spread_wide_ceiling_bps: float = Field(
        default=400.0,
        description="Hard spread veto above this (bps), regardless of EWMA.",
    )
    # Per-symbol notional cap (% equity). RiskManager sizes trades to
    # min(max_risk_pct, remaining headroom here) so this need not equal max_risk_pct.
    max_symbol_notional_pct: float = 1.0   # per-symbol exposure cap (% equity)
    # Coarse pre-trade headroom: requires (1 - (gross+add)/equity) >= this.
    # On leveraged futures, gross_notional often exceeds equity, so any value
    # > 0 vetoes most entries; use 0 (default) and rely on max_risk_pct /
    # max_gross_notional, or set MIN_FREE_MARGIN_PCT in .env if you want a buffer.
    min_free_margin_pct: float = 0.0
    # In-flight execution
    max_open_parents: int = 16              # max simultaneous in-flight parents
    submit_rate_per_sec: float = 5.0        # global REST submit throttle
    reject_cooldown_sec: float = 30.0       # symbol pause after K rejects
    max_consecutive_rejects: int = 3
    # Portfolio guards
    daily_loss_kill_pct: float = 0.08       # MAJOR: daily-loss kill
    max_consecutive_losses: int = 15        # MAJOR: losing-trade streak before latch
    consecutive_loss_min_abs_usd: float = Field(
        default=1.0,
        ge=0.0,
        description=(
            "If > 0, realised losses smaller than this |PnL| in quote terms do "
            "not advance the consecutive-loss streak (fees/noise)."
        ),
    )
    hwm_drawdown_kill_pct: float = 0.15     # MAJOR: high-water-mark drawdown
    exec_quality_kill_bps: float = 50.0     # MAJOR: rolling slippage kill
    exec_quality_window: int = 10           # number of completed parents to avg
    # System-level
    ws_stale_pause_sec: float = 30.0        # auto-pause on WS silence
    # User-data stream can be quiet for minutes when flat; use a longer threshold.
    user_data_stale_sec: float = 180.0
    # Mid priming: wait for ``bookTicker`` before falling back to REST ``/depth``.
    prime_ws_timeout_sec: float = 10.0
    reconcile_interval_sec: float = 120.0   # gateway-state reconcile cadence
    reconcile_qty_tolerance: float = 1e-6   # qty mismatch threshold
    # When True, periodic reconcile skips GET /account + /positionRisk while the
    # user-data stream (ORDER_TRADE_UPDATE, ACCOUNT_UPDATE) was active within
    # ``reconcile_user_data_fresh_sec`` — same live path Binance recommends
    # instead of REST polling. Set False to always verify via REST.
    reconcile_skip_rest_when_user_data_fresh: bool = True
    reconcile_user_data_fresh_sec: float = 120.0
    # When REST qty differs from local, overwrite local from venue and still trip
    # ``reconcile_mismatch`` so operators are alerted.
    reconcile_heal_on_mismatch: bool = True
    flatten_on_stop: bool = True            # market-out residuals on engine.stop()
    flatten_timeout_sec: float = 30.0
    flatten_poll_sec: float = 2.0         # venue-flat poll interval during operator flatten
    flatten_rounds: int = 2               # close attempts inside flatten() before wait-loop retries
    # Flatten execution: market for tiny/wide/retry; passive VWAP for large+tight;
    # aggressive VWAP (short schedule + market fallback) otherwise.
    flatten_market_max_notional_usd: float = 250.0
    flatten_vwap_min_notional_usd: float = 1_500.0
    flatten_passive_spread_bps: float = 20.0
    flatten_wide_spread_bps: float = 100.0
    flatten_vwap_duration_sec: int = 18
    flatten_vwap_slices: int = 4
    # Breaker lifecycle
    breaker_minor_cooldown_sec: float = 60.0
    # Per-code enable flags (see engine.risk.breaker_registry). operator_halt is always on.
    breaker_enabled: dict[str, bool] = Field(default_factory=default_breaker_enabled_map)
    # After auto-flatten for consecutive_losses, clear the latch so paper runs recover.
    auto_rearm_consecutive_losses_after_flatten: bool = True

    # --- Pre-trade validation (PreTradeValidator) ---
    max_order_notional_usd: float = 0.0   # 0 = disabled; absolute USD cap per order
    max_qty_vs_position_multiple: float = 0.0  # 0 = disabled; max entry qty vs |position|
    signal_dedup_ttl_sec: float = 2.0       # suppress duplicate signals within window
    max_limit_deviation_bps: float = 50.0   # LIMIT peg collar vs mid (execution layer)

    # --- Order reconciliation ---
    reconcile_cancel_orphans: bool = True   # auto-cancel venue orders unknown to OMS
    order_reconcile_on_startup: bool = True

    # --- Execution (AlgoWheel + calibration) ---
    imbalance_threshold: float = 0.20
    hit_ratio_threshold: float = 0.60
    symbol_calibration_path: str = "symbol_calibration.json"

    # --- Execution urgency ---
    urgent_score_threshold: float = 0.85    # Signal.score at/above → AGGRESSIVE
    urgent_duration_sec: int = 10
    urgent_num_slices: int = 2
    urgent_max_slippage_bps: float = 20.0

    # --- Journal / observability ---
    journal_enabled: bool = True
    # Analytics worker: separate process for backtest/download (multicore isolation).
    analytics_worker_enabled: bool = True
    analytics_worker_mode: str = "embedded"  # embedded | external | disabled
    analytics_jobs_dir: str = "data/jobs"
    recover_on_start: bool = False
    latency_metrics_interval_sec: float = 5.0
    alert_webhook_url: str = ""
    alert_cooldown_sec: float = 60.0
    post_only_enabled: bool = False
    # Peg MM entry quotes at venue best bid / best ask (passive touch).
    mm_quote_at_touch: bool = False
    # When >0 and MM_QUOTE_AT_TOUCH=false, peg bid at best_bid + N*tick and ask at best_ask - N*tick.
    mm_quote_inside_touch_ticks: int = 1
    per_symbol_submit_rate: float = 0.0  # 0 = global only; else max submits/sec per symbol
    md_stale_resnapshot_sec: float = 30.0
    # Deprecated: use breaker_enabled["md_crossed_book"]. Kept for .env compatibility.
    md_crossed_book_breaker: bool = True
    clock_skew_warn_ms: float = 500.0

    @field_validator("breaker_enabled", mode="before")
    @classmethod
    def _normalize_breaker_enabled(cls, value: object) -> dict[str, bool]:
        if value is None:
            return default_breaker_enabled_map()
        if not isinstance(value, dict):
            return default_breaker_enabled_map()
        return merge_breaker_enabled({str(k): bool(v) for k, v in value.items()})
    api_token: str = ""  # when set, required on mutating /api/control/* routes
    prune_runs_older_than_days: int = 0  # 0 = disabled

