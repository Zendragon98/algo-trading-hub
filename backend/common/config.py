"""Runtime configuration.

All knobs are loaded from `backend/.env` (or actual environment variables)
via `pydantic-settings`. Nothing else in the codebase should call
`os.getenv` directly; depend on `Settings` instead so defaults and
validation live in one place.
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
    # behaviour (synthetic impact, log volume, kill-switch sensitivity).
    venue: str = "binance"
    trading_mode: TradingMode = TradingMode.PAPER

    # --- Binance ---
    binance_api_key: str = Field(default="", description="Futures API key")
    binance_api_secret: str = Field(default="", description="Futures API secret")
    binance_testnet: bool = True
    binance_rest_base: str = "https://testnet.binancefuture.com"
    binance_ws_base: str = "wss://stream.binancefuture.com"

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

    # --- Risk ---
    max_risk_pct: float = 0.35
    max_gross_notional: float = 50_000.0
    max_drawdown_pct: float = 0.10
    default_stop_loss_pct: float = 0.005
    default_take_profit_pct: float = 0.010

    # --- Execution ---
    vwap_duration_sec: int = 60
    vwap_num_slices: int = 6
    imbalance_top_n: int = 10
    trade_tape_window_sec: int = 300

    # --- Synthetic market impact (testnet only) ---
    # Testnet liquidity is paper-thin, so real fills look unrealistically clean.
    # When enabled, the engine adjusts each recorded fill price by an estimated
    # impact cost so the dashboard's PnL reflects what mainnet would look like.
    # Real testnet orders still execute; only the in-engine accounting changes.
    impact_model_enabled: bool = True
    impact_k: float = 0.5            # square-root model coefficient
    impact_min_depth: float = 1e-9   # floor on consumable depth to avoid div-by-zero

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
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

    @field_validator("symbols", "cors_origins", mode="before")
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
        return [(usdt[base], usdc[base]) for base in usdt.keys() & usdc.keys()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton settings instance."""
    return Settings()
