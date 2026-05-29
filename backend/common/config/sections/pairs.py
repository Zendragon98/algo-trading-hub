from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class PairsMixin(BaseModel):
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
    pair_entry_z: float = 2.5
    pair_exit_z: float = 0.4
    pair_stop_z: float = 3.5
    # Rolling window for z-score history. With ``pair_bar_sec > 0`` this is
    # interpreted as *bar count* (e.g. 600 bars × 60s = 10h); otherwise seconds.
    pair_z_window_sec: int = 600
    # Reference basis: ``btc_anchor`` (BTC basis only), ``weighted`` (24h vol
    # mean across coins), or ``independent`` (each coin z-scores raw basis).
    pair_reference_mode: str = "btc_anchor"
    # Bar-aggregate deviation samples (0 = every tick). 60 = one sample/min.
    pair_bar_sec: int = 60
    # Anti-churn guards. These exist to prevent "flip-flop" trading when z
    # oscillates around the entry/exit thresholds or when partial fills arrive
    # across multiple ticks.
    pair_min_hold_sec: int = 75
    pair_cooldown_sec: int = 120
    # Longer cooldown after a basis stop-out (seconds until re-entry allowed).
    pair_stop_cooldown_sec: int = 300
    pair_pending_timeout_sec: int = 120
    # Force-unwind open pairs held longer than this (0 = disabled).
    pair_max_hold_sec: int = 1800
    # Hybrid-sizing ceiling for pairs entries. The strategy scales qty
    # linearly with |z|/entry_z above the entry floor, capped at this
    # multiplier so a transient z-spike can't blow up the leg notional.
    pair_size_scale_cap: float = 2.0
    # Hard USD notional cap per leg (0 = disabled).
    pair_max_leg_notional: float = 500.0
    # Per-leg absolute loss cap in USD while open (0 = disabled).
    pair_max_leg_loss_usd: float = 25.0
    # Cap new pair opens per tick (exits/partials are not capped). 0 = unlimited.
    pair_max_new_entries_per_tick: int = 2
    # Minimum deviation samples before z-score is trusted. With 60s bars,
    # 30 samples ≈ 30 minutes warmup.
    pair_min_z_samples: int = 30
    # Skip pairs where either leg mid is below this (avoids sub-$0.001 memes
    # that often trip MIN_NOTIONAL / tick-size quirks on testnet).
    pair_min_mid_price: float = 0.01
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

