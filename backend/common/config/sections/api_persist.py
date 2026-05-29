from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

class ApiPersistMixin(BaseModel):
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
