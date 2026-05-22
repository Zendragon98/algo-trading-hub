"""Runtime configuration.

Every tunable default lives on `Settings` below — edit this file for risk,
spread, strategy, and other non-secret defaults. Use `backend/.env` or process
environment variables only for secrets, URLs, or deployment-specific overrides
(keys remain overridable via env when needed). The `.env.example` template stays
minimal (mostly API keys).

Nothing else in the codebase should call `os.getenv` directly; depend on
`Settings` / `get_settings()` instead.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .enums import TradingMode

# Resolve .env relative to this file rather than the cwd, so the engine can
# be launched from anywhere (run.bat, pytest, or `python -m`).
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Strongly-typed view of the environment.

    Construct via `get_settings()` to benefit from process-wide caching;
    instantiating directly re-reads the .env file each time.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Venue selection + trading mode ---
    # `venue` picks the gateway adapter (`gateways/factory.py`).
    # `trading_mode` is venue-agnostic and controls cross-venue safety
    # behaviour (log volume, kill-switch sensitivity).
    venue: str = "binance"
    trading_mode: TradingMode = TradingMode.PAPER

    # --- Binance ---
    binance_api_key: str = Field(default="", description="Futures API key")
    binance_api_secret: str = Field(default="", description="Futures API secret")
    binance_testnet: bool = True
    binance_rest_base: str = "https://testnet.binancefuture.com"
    binance_ws_base: str = "wss://stream.binancefuture.com"
    # Client-side REST pacing + HTTP 429 handling (see BinanceRestClient).
    # Slightly conservative default to stay under Binance futures REST weight limits
    # when connect + reconcile + orders share one client.
    binance_rest_min_interval_ms: int = 150
    binance_rest_429_default_backoff_sec: float = 60.0
    binance_rest_pause_buffer_sec: float = 0.5
    # Public market-data WS keepalive (see market_connection.py).
    market_ws_ping_interval_sec: float = 20.0
    market_ws_ping_timeout_sec: float = 180.0
    # Per-shard ingest queue between the socket reader and MD handlers (backpressure).
    market_ws_shard_queue_size: int = 4096
    # Debounce coalesced L2 REST resync after market WS shard reconnects.
    market_ws_reconnect_resync_delay_sec: float = 3.0
    # Bounded parallel REST ``/depth`` during book resync (startup vs reconnect).
    book_resync_concurrency: int = 4
    book_resync_reconnect_concurrency: int = 2

    # --- IBKR (Interactive Brokers) ---
    # Defaults match the canonical paper-trading IB Gateway / TWS port (7497).
    # Switch to 7496 for the live port. host/client_id are passed straight
    # through to ib_async / ib_insync when that adapter is implemented.
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 7
    ibkr_account: str = ""

    # --- Engine ---
    # Annotated[..., NoDecode] tells pydantic-settings to skip JSON parsing
    # so the env loader hands us the raw string for `_split_csv` to split.
    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "BTCUSDC"]
    )
    base_currency: str = "USDT"
    engine_autostart: bool = False
    # Which strategy set to run: "pairs" | "sma" | "market_making" | "market_making_v2" | "all".
    # "all" runs every registered strategy with internal position netting.
    strategy: str = "pairs"

    # --- Risk ---
    # `max_risk_pct` is the hard ceiling on per-leg notional (% of equity).
    # `risk_per_trade_pct` is the *primary* sizing input: the fraction of
    # equity the engine is willing to lose if `default_stop_loss_pct` is
    # hit. Position size is then `(equity * risk_per_trade_pct) / stop_pct`,
    # which is the canonical "size by stop" rule traders use on futures.
    max_risk_pct: float = 0.35
    risk_per_trade_pct: float = 0.003
    max_gross_notional: float = 50_000.0
    max_drawdown_pct: float = 0.10
    default_stop_loss_pct: float = 0.005
    default_take_profit_pct: float = 0.010

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
    max_symbol_notional_pct: float = 0.20   # per-symbol exposure cap
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
    daily_loss_kill_pct: float = 0.05       # MAJOR: daily-loss kill
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
    symbol_calibration_path: str = ""

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
    per_symbol_submit_rate: float = 0.0  # 0 = global only; else max submits/sec per symbol
    md_stale_resnapshot_sec: float = 30.0
    md_crossed_book_breaker: bool = True
    clock_skew_warn_ms: float = 500.0
    api_token: str = ""  # when set, required on mutating /api/control/* routes
    prune_runs_older_than_days: int = 0  # 0 = disabled

    # --- Pairs-trading risk (basis-spread space) ---
    # The pairs strategy enters when |z| >= pair_entry_z, takes profit
    # when |z| <= pair_exit_z (basis converged), and stops out when |z|
    # diverges past pair_stop_z against the open direction.
    #
    # These are the *natural* risk knobs for a basis trade: a pair's
    # actual P&L is driven by the basis spread, not the individual legs'
    # absolute moves. Because of this the per-leg fixed-% SL/TP above
    # (`default_stop_loss_pct` / `default_take_profit_pct`) is bypassed
    # for symbols owned by a pairs strategy — see
    # `engine.risk.stop_loss.StopLossMonitor` and
    # `engine.strategies.strategy_base.StrategyBase.manages_own_risk`.
    pair_calibration_path: str = ""  # optional JSON from analytics.pair_analyzer
    pair_entry_z: float = 3.0
    pair_exit_z: float = 0.35
    pair_stop_z: float = 4.0
    # Rolling window for historical z-score estimation (seconds).
    pair_z_window_sec: int = 600
    # Anti-churn guards. These exist to prevent "flip-flop" trading when z
    # oscillates around the entry/exit thresholds or when partial fills arrive
    # across multiple ticks.
    pair_min_hold_sec: int = 75
    pair_cooldown_sec: int = 90
    pair_pending_timeout_sec: int = 120
    # Hybrid-sizing ceiling for pairs entries. The strategy scales qty
    # linearly with |z|/entry_z above the entry floor, capped at this
    # multiplier so a transient z-spike can't blow up the leg notional.
    pair_size_scale_cap: float = 1.15
    # Cap new pair opens per tick (exits/partials are not capped). 0 = unlimited.
    pair_max_new_entries_per_tick: int = 2
    # Minimum deviation samples before z-score is trusted (1Hz ticks ≈ seconds).
    pair_min_z_samples: int = 30
    # Skip pairs where either leg mid is below this (avoids sub-$0.001 memes
    # that often trip MIN_NOTIONAL / tick-size quirks on testnet).
    pair_min_mid_price: float = 0.001
    # Abort a one-legged pending open after this many seconds (reduce-only unwind).
    pair_partial_fill_abort_sec: int = 90
    # Signal.score floor on pair entries so the router uses urgent slicing.
    pair_urgent_score: float = 0.85
    # Prefer Binance public WS ``!ticker@arr`` for 24h quote volume instead of
    # polling REST ``/ticker/24hr``. When False, periodic refresh always uses REST.
    pair_volume_from_websocket: bool = True
    # How often the volume-weight refresh loop runs. With ``pair_volume_from_websocket``,
    # REST is used only for symbols still missing from WS (or the whole set if False).
    pair_volume_refresh_sec: int = 1800
    # Extra GET /fapi/v2/account polls behind the live ACCOUNT_UPDATE stream.
    # ``0`` disables this loop so balances refresh only via
    # ``RECONCILE_INTERVAL_SEC`` + WS (avoids doubling REST load with the
    # reconciler). Set e.g. ``300`` if you want an additional safety net.
    balance_resync_sec: int = 0

    # --- Futures leverage ---
    # Applied per symbol via the venue's `set_leverage` hook lazily before
    # the first entry order for that symbol (not at engine start). Leverage
    # doesn't change the dollar-loss-at-stop (that's
    # bounded by `risk_per_trade_pct`); it only relaxes the margin
    # requirement so the stop-loss-sized notional fits in the wallet.
    leverage: int = 10
    # Binance: caps per symbol come from GET /fapi/v1/leverageBracket.
    # Cached under backend/data/cache/ so later starts skip the REST call.
    # `0` = no time-based refresh (only refetch if file missing or
    # BINANCE_REST_BASE changes). Set >0 (seconds) to periodically refresh.
    leverage_bracket_cache_path: str = "data/cache/binance_leverage_brackets.json"
    leverage_bracket_cache_ttl_sec: int = 0

    # --- Execution ---
    vwap_duration_sec: int = 60
    vwap_num_slices: int = 6
    imbalance_top_n: int = 10
    trade_tape_window_sec: int = 300

    # --- SMA crossover strategy (multi-symbol scanner) ---
    # SMA_SYMBOLS supports a CSV list ("BTCUSDT,ETHUSDT") or the literal
    # "AUTO" to discover every USDT perpetual on the venue at boot.
    # SMA_SYMBOL is kept as a backwards-compat shim — when sma_symbols is
    # empty, main.py falls back to a single-symbol list of [sma_symbol].
    sma_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
    )
    sma_symbol: str = "BTCUSDT"
    sma_fast_window: int = 8
    sma_slow_window: int = 24
    # When >0, each SMA sample is one *closed bar* of this length (seconds),
    # using the last mid seen in that bar as the close — windows are then in
    # bar count (intraday-style). When 0, one sample per engine heartbeat (~1Hz),
    # so SMA_FAST_WINDOW/SMA_SLOW_WINDOW count ticks (e.g. 8/24 ≈ 8–24s), not minutes.
    sma_bar_interval_sec: float = Field(default=0.0)
    # Skip symbols below this mid (stops cannot resolve on sub-tick alts).
    sma_min_mid_price: float = 0.01
    # Portfolio risk budget per round-trip (split evenly across ``sma_symbols``).
    # Falls back to ``sma_qty`` when equity is unavailable (e.g. boot
    # before the first ``fetch_balance`` lands).
    sma_risk_per_trade_pct: float = 0.002
    sma_qty: float = 0.001
    sma_cooldown_sec: int = 45
    sma_max_entries_per_tick: int = 2
    # INFO heartbeat while the SMA scanner is active (0 = off).
    sma_scan_log_interval_sec: float = 60.0
    # Cap SMA_SYMBOLS=AUTO to the top-N USDT perps by 24h quote volume (0 = full universe).
    sma_max_symbols: int = 10

    # --- Blended multi-indicator strategy (EMA + MACD + RSI + BB + micro) ---
    blend_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
    )
    blend_symbol: str = "BTCUSDT"
    blend_bar_interval_sec: float = 300.0
    blend_ema_fast: int = 9
    blend_ema_slow: int = 21
    blend_macd_fast: int = 12
    blend_macd_slow: int = 26
    blend_macd_signal: int = 9
    blend_rsi_period: int = 14
    blend_rsi_long_low: float = 45.0
    blend_rsi_long_high: float = 70.0
    blend_rsi_short_low: float = 30.0
    blend_rsi_short_high: float = 55.0
    blend_rsi_overbought: float = 75.0
    blend_rsi_oversold: float = 25.0
    blend_bb_period: int = 20
    blend_bb_std: float = 2.0
    blend_bb_long_pct: float = 0.2
    blend_bb_short_pct: float = 0.8
    blend_weight_ema: float = 1.0
    blend_weight_macd: float = 1.0
    blend_weight_rsi: float = 0.8
    blend_weight_bb: float = 0.7
    blend_weight_micro: float = 0.5
    blend_micro_imbalance_scale: float = 4.0
    blend_micro_tape_scale: float = 6.0
    blend_micro_threshold: float = 0.12
    blend_entry_threshold: float = 0.35
    blend_exit_threshold: float = 0.1
    blend_min_confirming_votes: int = 3
    blend_min_mid_price: float = 0.01
    blend_risk_per_trade_pct: float = 0.002
    blend_qty: float = 0.001
    blend_cooldown_sec: float = 60.0
    blend_max_entries_per_tick: int = 2
    blend_scan_log_interval_sec: float = 60.0

    # --- Market-making tilt strategy (skew + imbalance + tape) ---
    # MM_SYMBOLS: CSV list, or ``AUTO`` to run analytics mm_universe_scanner at boot.
    mm_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "XRPUSDT",
            "DOGEUSDT",
            "ADAUSDT",
            "AVAXUSDT",
        ],
    )
    # Rolling mean of (micro_price - mid)/mid in bps over this many seconds.
    mm_skew_window_sec: float = 300.0
    mm_skew_scale: float = 1.0
    mm_imbalance_scale: float = 8.0
    # Count-based tape pressure uses TRADE_TAPE_WINDOW_SEC (default 300s): scale on
    # (ask_hit_count - bid_hit_count) / total_trades when total >= mm_min_tape_trades.
    mm_tape_scale: float = 12.0
    mm_min_tape_trades: int = 3
    mm_min_samples: int = 5
    mm_risk_per_trade_pct: float = 0.002
    mm_qty: float = 0.001
    mm_cooldown_sec: float = 20.0
    # Cap new MM entries per engine tick (exits are not capped). 0 = unlimited.
    mm_max_entries_per_tick: int = 1
    # MM_SYMBOLS=AUTO: analytics scan for liquid, stable-spread markets (see mm_universe_scanner).
    mm_auto_max_symbols: int = 12
    mm_auto_prefilter_top_volume: int = 60
    mm_auto_sample_rounds: int = 20
    mm_auto_sample_interval_sec: float = 1.0
    mm_auto_min_quote_volume: float = 5_000_000.0
    mm_auto_min_mid_price: float = 0.05
    mm_auto_min_spread_bps: float = 0.8
    mm_auto_max_spread_bps: float = 20.0
    # Stability caps: 0 = derive from scan percentiles + 24h range vol (see mm_universe_scanner).
    mm_auto_max_spread_cv: float = 0.0
    mm_auto_max_mid_vol_bps: float = 0.0
    mm_auto_stability_percentile: float = 75.0
    mm_auto_spread_cv_floor: float = 0.12
    mm_auto_spread_cv_cap: float = 0.70
    mm_auto_mid_vol_floor_bps: float = 2.0
    mm_auto_mid_vol_cap_bps: float = 35.0
    mm_auto_vol_regime_mult: float = 1.25
    mm_auto_min_edge_bps: float = 0.0  # 0 = 2× maker fee + spread buffer
    mm_auto_scan_ttl_sec: float = 3600.0
    # Set at boot when MM_SYMBOLS/MM2_SYMBOLS were AUTO; enables live universe refresh.
    mm_universe_auto: bool = False
    mm2_universe_auto: bool = False
    mm_universe_refresh_sec: float = 3600.0
    mm_universe_adverse_refresh_cooldown_sec: float = 600.0
    mm_universe_adverse_check_sec: float = 30.0
    mm_universe_adverse_markout_bps: float = 0.0
    mm_universe_adverse_min_symbols: int = 2
    mm_universe_adverse_spread_widen_mult: float = 1.75
    mm_universe_adverse_regime_vol_bps: float = 25.0
    mm_universe_regime_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"],
    )

    # --- Market-making 2.0 (fee-aware fade; skew + imbalance + tape) ---
    mm2_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "XRPUSDT",
            "DOGEUSDT",
            "ADAUSDT",
            "AVAXUSDT",
        ],
    )
    mm2_skew_window_sec: float = 300.0
    mm2_skew_scale: float = 1.0
    mm2_imbalance_scale: float = 8.0
    mm2_tape_scale: float = 12.0
    mm2_min_tape_trades: int = 5
    mm2_min_skew_bps: float = 1.0
    mm2_tape_confirm: float = 0.08
    mm2_taker_fee_bps: float = 4.0
    mm2_maker_fee_bps: float = 2.0
    mm2_fee_round_trip_bps: float = 0.0
    mm2_spread_buffer_bps: float = 2.0
    mm2_min_spread_bps: float = 0.0
    mm2_min_edge_bps: float = 0.0
    mm2_min_exit_profit_bps: float = 5.0
    mm2_max_hold_sec: float = 150.0
    mm2_min_samples: int = 5
    mm2_risk_per_trade_pct: float = 0.002
    mm2_qty: float = 0.001
    mm2_cooldown_sec: float = 25.0
    mm2_max_entries_per_tick: int = 1
    # When >0, require |composite| >= entry + fee_rt×scale (composite-fee gate).
    # 0 = off (default); use MM2_MIN_SPREAD_BPS for a literal spread floor instead.
    mm2_composite_fee_scale: float = 0.0
    mm2_scan_log_interval_sec: float = 60.0

    # --- Institutional MM (quote-only execution, microstructure) ---
    mm_institutional_risk_enabled: bool = True
    mm_quote_enabled: bool = True
    mm_urgent_exit_market: bool = False
    mm_tape_pressure_mode: str = "volume"  # volume | count
    mm_max_inventory_notional: float = 0.0  # 0 = use max_symbol_notional_pct * equity
    mm_inventory_skew_scale: float = 4.0
    # Shift MM reservation mid away from inventory at |ratio|=1 (bps; long -> lower mid).
    mm_reservation_inventory_bps: float = 12.0
    # Extra half-spread on the side that would add exposure at |ratio|=1 (bps).
    mm_inventory_spread_skew_bps: float = 5.0
    # Weight on microstructure bias when building reservation mid (0–1 scale on bias units).
    mm_reservation_micro_weight: float = 0.12
    mm_inventory_hard_ratio: float = 0.85
    mm_inventory_exit_ratio: float = 0.7
    mm_inventory_size_damp: float = 0.5
    mm_inventory_include_working: bool = False
    mm_jump_return_bps: float = 25.0
    mm_jump_vol_mult: float = 3.0
    mm_jump_vol_ewma_alpha: float = 0.08
    mm_jump_pause_sec: float = 30.0
    mm_jump_flatten: bool = False
    mm_max_adverse_markout_bps: float = 8.0
    mm_markout_cooldown_sec: float = 15.0
    mm_scratch_loss_bps: float = 15.0
    mm_exit_scratch_bps: float = 5.0
    mm_min_exit_profit_bps: float = 5.0
    mm_max_hold_sec: float = 150.0
    mm_catastrophe_stop_pct: float = 0.0
    mm_depletion_top_n: int = 10
    mm_depletion_baseline_alpha: float = 0.06
    mm_depletion_drop_pct: float = 0.25
    mm_depletion_window_sec: float = 5.0
    mm_depletion_widen_bps: float = 4.0
    mm_depletion_shift_bps: float = 3.0
    mm_depletion_size_damp: float = 0.4
    mm_depletion_pull_ratio: float = 0.35
    mm_depletion_breaker_ratio: float = 0.25
    mm_depletion_scale: float = 6.0
    mm_large_trade_mult: float = 3.0
    mm_toxicity_threshold: float = 0.65
    mm_toxicity_vpin_weight: float = 0.2
    mm_toxicity_large_weight: float = 0.15
    mm_toxicity_depletion_weight: float = 0.2
    mm_toxicity_markout_weight: float = 0.2
    mm_toxicity_jump_weight: float = 0.15
    mm_toxicity_tape_vel_weight: float = 0.05
    mm_toxicity_informed_weight: float = 0.25
    mm_toxicity_markout_norm_bps: float = 20.0
    mm_toxicity_tape_vel_norm: float = 50.0
    mm_toxicity_vpin_informed_high: float = 0.55
    mm_toxicity_vpin_informed_low: float = 0.45
    mm_toxicity_depletion_informed_min: float = 0.5
    mm_quote_half_spread_bps: float = 3.0
    # Per-symbol half-spread (bps). Env: JSON or ``BTCUSDT:2,ETHUSDT:3,DOGEUSDT:12``.
    mm_symbol_half_spread_bps: Annotated[dict[str, float], NoDecode] = Field(
        default_factory=dict,
    )
    # Per-symbol full overrides, e.g. ``{"DOGEUSDT":{"half_spread_bps":15,"min_spread_bps":8}}``.
    mm_symbol_quote_overrides: Annotated[dict[str, dict[str, float]], NoDecode] = Field(
        default_factory=dict,
    )
    # When true, half-spread is at least ``mm_quote_venue_spread_mult * venue_spread_bps / 2``.
    mm_quote_use_venue_spread_floor: bool = True
    mm_quote_venue_spread_mult: float = 1.0
    # L2 calibration artefact from analytics.spread_calibrator (after l2_loader ingest).
    mm_spread_calibration_path: str = "data/mm_spread_calibration.json"
    mm_spread_calib_percentile: float = 50.0
    mm_spread_calib_half_mult: float = 0.55
    mm_spread_calib_buffer_bps: float = 0.5
    mm_spread_calib_min_half_bps: float = 1.0
    mm_spread_calib_max_half_bps: float = 50.0
    mm_spread_calib_min_samples: int = 30
    mm_quote_refresh_bps: float = 1.0
    mm_quote_min_rest_sec: float = 0.5
    mm_quote_size_pct: float = 0.002
    mm_quote_max_refresh_per_tick: int = 8
    mm_quote_toxic_widen_bps: float = 6.0

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # GET /api/klines dedupes identical upstream REST calls within this TTL (seconds).
    klines_cache_ttl_sec: float = 60.0
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # --- Persistence (per-run on-disk archive) ---
    # Each engine start creates a fresh `<persist_dir>/<run_id>/` folder
    # with a rotating `app.log` and one JSONL file per event stream so a
    # session can be replayed and analysed offline.
    persist_enabled: bool = True
    persist_dir: str = "data/runs"
    persist_record_ticks: bool = False    # firehose; off by default
    capture_market_bars: bool = True    # 1m OHLCV from live mids → backtest library
    capture_bar_interval_sec: int = 60
    capture_flush_interval_sec: float = 300.0
    backtest_slippage_bps: float = 5.0
    backtest_initial_equity: float = 10_000.0
    log_level: str = "info"  # debug | info | warning | error — env LOG_LEVEL
    log_file_enabled: bool = True
    log_file_max_bytes: int = 10_000_000  # 10 MB before rotation
    log_file_backup_count: int = 5

    @field_validator("mm_symbol_half_spread_bps", mode="before")
    @classmethod
    def _parse_mm_symbol_half_spread(cls, value: object) -> object:
        return cls._parse_symbol_float_map(value)

    @field_validator("mm_symbol_quote_overrides", mode="before")
    @classmethod
    def _parse_mm_symbol_overrides(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            value = json.loads(text)
        if not isinstance(value, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for sym_key, fields in value.items():
            sym = str(sym_key).strip().upper()
            if not sym or not isinstance(fields, dict):
                continue
            out[sym] = {str(k): float(v) for k, v in fields.items()}
        return out

    @staticmethod
    def _parse_symbol_float_map(value: object) -> dict[str, float]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {str(k).strip().upper(): float(v) for k, v in value.items() if str(k).strip()}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            if text.startswith("{"):
                raw: Any = json.loads(text)
                return {str(k).strip().upper(): float(v) for k, v in raw.items() if str(k).strip()}
            out: dict[str, float] = {}
            for part in text.split(","):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                sym, val = part.split(":", 1)
                out[sym.strip().upper()] = float(val.strip())
            return out
        return {}

    @field_validator(
        "symbols",
        "sma_symbols",
        "blend_symbols",
        "mm_symbols",
        "mm2_symbols",
        "mm_universe_regime_symbols",
        "cors_origins",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # pydantic-settings hands us a raw string from the .env file; split
        # it into a list so callers always see `list[str]`.
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("venue", mode="before")
    @classmethod
    def _normalise_venue(cls, value: object) -> object:
        # Case-insensitive venue ids so VENUE=Binance and VENUE=binance both work.
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def is_live(self) -> bool:
        """Convenience: True when running against real money."""
        return self.trading_mode is TradingMode.LIVE

    @property
    def env_path(self) -> Path:
        """Path the settings were loaded from. Helpful in error messages."""
        return _ENV_PATH

    def pair_legs(self) -> list[tuple[str, str]]:
        """Return (USDT-leg, USDC-leg) tuples derived from `symbols`.

        The pairs-trading strategy expects matched pairs like
        ``("BTCUSDT", "BTCUSDC")``. Any symbol without a partner is dropped
        and reported by the strategy at startup, not here.
        """
        usdt = {s.removesuffix("USDT"): s for s in self.symbols if s.endswith("USDT")}
        usdc = {s.removesuffix("USDC"): s for s in self.symbols if s.endswith("USDC")}
        bases = sorted(usdt.keys() & usdc.keys())
        return [(usdt[base], usdc[base]) for base in bases]


def normalize_strategy_name(value: str) -> str:
    """Map short aliases to ``StrategyBase.name`` ids (same as ``main.py`` boot logic)."""

    aliases: dict[str, str] = {
        "pairs": "pairs_trading_usdt_usdc",
        "pairs_trading": "pairs_trading_usdt_usdc",
        "sma": "sma_crossover",
        "blend": "blended_signals",
        "blended": "blended_signals",
        "blended_signals": "blended_signals",
        "mm": "market_making",
        "market_making": "market_making",
        "mm2": "market_making_v2",
        "market_making_v2": "market_making_v2",
        "all": "all",
        "multi": "all",
    }
    k = (value or "").strip().lower()
    return aliases.get(k, k)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton settings instance."""
    return Settings()
