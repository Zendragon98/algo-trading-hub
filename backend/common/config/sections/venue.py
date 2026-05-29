from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from common.enums import TradingMode

class VenueMixin(BaseModel):
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
    binance_rest_min_interval_ms: int = 200
    binance_rest_429_default_backoff_sec: float = 60.0
    binance_rest_pause_buffer_sec: float = 0.5
    # Fail fast instead of blocking the event loop for multi-minute IP bans.
    binance_rest_max_blocking_wait_sec: float = 8.0
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

