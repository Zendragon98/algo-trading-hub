"""Pull historical data from Binance into the shared kline library.

CLI:
    python -m analytics.data_loader --symbols BTCUSDT,BTCUSDC --days 30
    python -m analytics.data_loader --symbols BTCUSDT --interval 1m --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass

import pandas as pd

from common.config import Settings, get_settings
from gateways.binance.rest_client import BinanceRestClient
from gateways.binance.universe import discover_usdt_usdc_pairs

from .kline_store import KLINE_COLS, merge_into_library

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadResult:
    symbol: str
    interval: str
    rows: int
    path: str


async def fetch_klines(
    rest: BinanceRestClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Fetch klines in 1500-row pages until ``end_ms`` is reached."""
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
        cursor = int(page[-1][6]) + 1

    if not chunks:
        return pd.DataFrame(columns=KLINE_COLS)

    flat = [row for chunk in chunks for row in chunk]
    df = pd.DataFrame(flat, columns=KLINE_COLS)
    for col in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_base",
        "taker_buy_quote",
    ):
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


async def download_klines(
    symbols: list[str],
    *,
    interval: str = "1m",
    days: int = 7,
    settings: Settings | None = None,
) -> list[DownloadResult]:
    """Download and merge klines for each symbol into the shared library."""
    settings = settings or get_settings()
    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    results: list[DownloadResult] = []
    try:
        resolved = symbols
        if len(resolved) == 1 and resolved[0].upper() == "AUTO":
            info = await rest.exchange_info()
            resolved = discover_usdt_usdc_pairs(info)
        for symbol in resolved:
            sym = symbol.strip().upper()
            logger.info("fetching klines %s interval=%s days=%d", sym, interval, days)
            df = await fetch_klines(rest, sym, interval, start_ms, end_ms)
            path = merge_into_library(df, sym, interval, source="download")
            results.append(
                DownloadResult(symbol=sym, interval=interval, rows=len(df), path=str(path))
            )
            logger.info("merged %d rows -> %s", len(df), path)
    finally:
        await rest.close()
    return results


async def _run_cli(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await download_klines(args.symbols, interval=args.interval, days=args.days)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical klines from Binance")
    parser.add_argument(
        "--symbols",
        type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
        required=True,
        help="Comma-separated list, e.g. BTCUSDT,BTCUSDC (or AUTO)",
    )
    parser.add_argument("--interval", default="1m", help="Kline interval (default: 1m)")
    parser.add_argument("--days", type=int, default=7, help="Lookback in days (default: 7)")
    args = parser.parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
