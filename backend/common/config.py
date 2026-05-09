"""Runtime configuration.

Every tunable default lives on `Settings` below. Operators override values
via `backend/.env` or real environment variables (`pydantic-settings`); the
`.env.example` file is intentionally minimal — copy it to `.env` and only
set secrets or knobs you change from defaults.

Nothing else in the codebase should call `os.getenv` directly; depend on
`Settings` / `get_settings()` instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

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
    binance_rest_min_interval_ms: int = 50
    binance_rest_429_default_backoff_sec: float = 60.0
    binance_rest_pause_buffer_sec: float = 0.5

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
    # Which strategy set to run. Supported: "pairs" | "sma".
    strategy: str = "pairs"

    # --- Risk ---
    # `max_risk_pct` is the hard ceiling on per-leg notional (% of equity).
    # `risk_per_trade_pct` is the *primary* sizing input: the fraction of
    # equity the engine is willing to lose if `default_stop_loss_pct` is
    # hit. Position size is then `(equity * risk_per_trade_pct) / stop_pct`,
    # which is the canonical "size by stop" rule traders use on futures.
    max_risk_pct: float = 0.35
    risk_per_trade_pct: float = 0.005
    max_gross_notional: float = 50_000.0
    max_drawdown_pct: float = 0.10
    default_stop_loss_pct: float = 0.005
    default_take_profit_pct: float = 0.010

    # --- Failsafes (circuit breakers) ---
    # Pre-trade gates
    max_tick_age_sec: float = 5.0           # stale-tick veto threshold
    max_entry_spread_bps: float = 25.0      # wide-spread veto threshold
    max_symbol_notional_pct: float = 0.20   # per-symbol exposure cap
    min_free_margin_pct: float = 0.10       # equity headroom required to enter
    # In-flight execution
    max_open_parents: int = 8               # max simultaneous in-flight parents
    submit_rate_per_sec: float = 5.0        # global REST submit throttle
    reject_cooldown_sec: float = 30.0       # symbol pause after K rejects
    max_consecutive_rejects: int = 3
    # Portfolio guards
    daily_loss_kill_pct: float = 0.05       # MAJOR: daily-loss kill
    max_consecutive_losses: int = 5         # MAJOR: streak kill
    hwm_drawdown_kill_pct: float = 0.15     # MAJOR: high-water-mark drawdown
    exec_quality_kill_bps: float = 50.0     # MAJOR: rolling slippage kill
    exec_quality_window: int = 10           # number of completed parents to avg
    # System-level
    ws_stale_pause_sec: float = 30.0        # auto-pause on WS silence
    # Mid priming: wait for ``bookTicker`` before falling back to REST ``/depth``.
    prime_ws_timeout_sec: float = 10.0
    reconcile_interval_sec: float = 60.0    # gateway-state reconcile cadence
    reconcile_qty_tolerance: float = 1e-6   # qty mismatch threshold
    # When True, periodic reconcile skips GET /account + /positionRisk while the
    # user-data stream (ORDER_TRADE_UPDATE, ACCOUNT_UPDATE) was active within
    # ``reconcile_user_data_fresh_sec`` — same live path Binance recommends
    # instead of REST polling. Set False to always verify via REST.
    reconcile_skip_rest_when_user_data_fresh: bool = True
    reconcile_user_data_fresh_sec: float = 120.0
    flatten_on_stop: bool = True            # market-out residuals on engine.stop()
    flatten_timeout_sec: float = 30.0
    # Breaker lifecycle
    breaker_minor_cooldown_sec: float = 60.0

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
    pair_entry_z: float = 2.0
    pair_exit_z: float = 0.5
    pair_stop_z: float = 4.0
    # Rolling window for historical z-score estimation (seconds).
    pair_z_window_sec: int = 600
    # Anti-churn guards. These exist to prevent "flip-flop" trading when z
    # oscillates around the entry/exit thresholds or when partial fills arrive
    # across multiple ticks.
    pair_min_hold_sec: int = 30
    pair_cooldown_sec: int = 15
    pair_pending_timeout_sec: int = 120
    # Hybrid-sizing ceiling for pairs entries. The strategy scales qty
    # linearly with |z|/entry_z above the entry floor, capped at this
    # multiplier so a transient z-spike can't blow up the leg notional.
    pair_size_scale_cap: float = 2.0
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
    # Applied per symbol via the venue's `set_leverage` hook on engine
    # start. Leverage doesn't change the dollar-loss-at-stop (that's
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
    sma_fast_window: int = 10
    sma_slow_window: int = 30
    # Per-symbol equity slice spent on each SMA entry. Default 0.5%.
    # Falls back to ``sma_qty`` when equity is unavailable (e.g. boot
    # before the first ``fetch_balance`` lands).
    sma_risk_per_trade_pct: float = 0.005
    sma_qty: float = 0.001
    sma_cooldown_sec: int = 15

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
    log_file_enabled: bool = True
    log_file_max_bytes: int = 10_000_000  # 10 MB before rotation
    log_file_backup_count: int = 5

    @field_validator("symbols", "sma_symbols", "cors_origins", mode="before")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton settings instance."""
    return Settings()
