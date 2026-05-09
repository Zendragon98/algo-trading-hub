"""Pull historical data from Binance Futures Testnet into parquet caches.

CLI:
    python -m analytics.data_loader --symbols BTCUSDT,BTCUSDC --days 30
    python -m analytics.data_loader --symbols BTCUSDT --interval 1m --days 7

Outputs land in `backend/data/` and are picked up by `pair_analyzer` and
`orderbook_analyzer` for offline calibration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

import pandas as pd

from common.config import get_settings
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_usdc_pairs

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base",
    "taker_buy_quote", "ignore",
]


async def fetch_klines(
    rest: BinanceRestClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Fetch klines in 1500-row pages until `end_ms` is reached."""
    chunks: list[list[list]] = []
    cursor = start_ms
    while cursor < end_ms:
        page = await rest.klines(
            symbol=symbol,
            interval=interval,
            start_ms=cursor,
            end_ms=end_ms,
            limit=1500,
        )
        if not page:
            break
        chunks.append(page)
        cursor = int(page[-1][6]) + 1   # close_time + 1ms

    if not chunks:
        return pd.DataFrame(columns=_KLINE_COLS)

    flat = [row for chunk in chunks for row in chunk]
    df = pd.DataFrame(flat, columns=_KLINE_COLS)
    # Cast strings to numerics; Binance returns everything as JSON strings.
    for col in ("open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_base", "taker_buy_quote"):
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.set_index("open_time")


async def _run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    _DATA_DIR.mkdir(exist_ok=True)

    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000

    try:
        symbols = args.symbols
        if len(symbols) == 1 and symbols[0].upper() == "AUTO":
            info = await rest.exchange_info()
            symbols = discover_usdt_usdc_pairs(info)
            logger.info("symbols=AUTO -> discovered %d symbols", len(symbols))

        for symbol in symbols:
            logger.info("fetching klines %s interval=%s days=%d", symbol, args.interval, args.days)
            df = await fetch_klines(rest, symbol, args.interval, start_ms, end_ms)
            path = _DATA_DIR / f"klines_{symbol}_{args.interval}.parquet"
            df.to_parquet(path)
            logger.info("wrote %d rows -> %s", len(df), path)
    finally:
        await rest.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical klines from Binance Futures Testnet")
    parser.add_argument(
        "--symbols",
        type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
        required=True,
        help="Comma-separated list, e.g. BTCUSDT,BTCUSDC (or AUTO)",
    )
    parser.add_argument("--interval", default="1m", help="Kline interval (default: 1m)")
    parser.add_argument("--days", type=int, default=7, help="Lookback in days (default: 7)")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
