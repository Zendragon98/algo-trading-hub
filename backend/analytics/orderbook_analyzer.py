"""Order-book imbalance & trade-tape statistics.

Streams agg-trade history for a symbol from the public REST endpoint
and produces:
    - distribution of taker-buy volume share over rolling windows
    - distribution of trade sizes (used to size slicer children)
    - empirical thresholds at the 80th/90th percentiles, fed back into
      the AlgoWheel's `imbalance_threshold` / `hit_ratio_threshold`.

Output: `data/orderbook_<symbol>.json`.

CLI:
    python -m analytics.orderbook_analyzer --symbol BTCUSDT --window-sec 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from common.config import get_settings
from gateways.binance.rest_client import BinanceRestClient

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class OrderbookReport:
    symbol: str
    samples: int
    window_sec: int
    median_taker_buy_share: float
    p80_taker_buy_share: float
    p90_taker_buy_share: float
    suggested_hit_ratio_threshold: float


async def _fetch_recent_trades(
    rest: BinanceRestClient, symbol: str, lookback_minutes: int
) -> list[dict]:
    """Pull recent agg trades by paging backwards from now."""
    end = int(time.time() * 1000)
    start = end - lookback_minutes * 60_000
    all_trades: list[dict] = []
    cursor = start
    while cursor < end:
        page = await rest.agg_trades(symbol=symbol, start_ms=cursor, end_ms=end, limit=1000)
        if not page:
            break
        all_trades.extend(page)
        cursor = int(page[-1]["T"]) + 1
    return all_trades


def _rolling_taker_buy_share(trades: list[dict], window_sec: int) -> list[float]:
    """For each trade, share of buy-init volume in the prior `window_sec`."""
    window = deque()  # (ts, qty, is_buy)
    sums = {"buy": 0.0, "total": 0.0}
    out: list[float] = []
    cutoff_ms = window_sec * 1000

    for trade in trades:
        ts = int(trade["T"])
        qty = float(trade["q"])
        is_buy = not trade.get("m", False)
        window.append((ts, qty, is_buy))
        sums["total"] += qty
        if is_buy:
            sums["buy"] += qty
        while window and window[0][0] < ts - cutoff_ms:
            evict_ts, evict_qty, evict_buy = window.popleft()
            sums["total"] -= evict_qty
            if evict_buy:
                sums["buy"] -= evict_qty
        if sums["total"] > 0:
            out.append(sums["buy"] / sums["total"])
    return out


async def _run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    _DATA_DIR.mkdir(exist_ok=True)
    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    try:
        logger.info("fetching agg trades %s lookback=%dmin", args.symbol, args.lookback_min)
        trades = await _fetch_recent_trades(rest, args.symbol, args.lookback_min)
    finally:
        await rest.close()

    series = _rolling_taker_buy_share(trades, args.window_sec)
    if not series:
        logger.warning("no trades returned for %s", args.symbol)
        return
    arr = np.asarray(series)
    report = OrderbookReport(
        symbol=args.symbol,
        samples=len(series),
        window_sec=args.window_sec,
        median_taker_buy_share=float(np.median(arr)),
        p80_taker_buy_share=float(np.percentile(arr, 80)),
        p90_taker_buy_share=float(np.percentile(arr, 90)),
        suggested_hit_ratio_threshold=float(np.percentile(arr, 80)),
    )
    path = _DATA_DIR / f"orderbook_{args.symbol}.json"
    path.write_text(json.dumps(asdict(report), indent=2))
    logger.info("wrote %s", path)
    print(json.dumps(asdict(report), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Trade-tape calibration for the AlgoWheel")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--lookback-min", type=int, default=60, help="Total lookback to fetch")
    parser.add_argument("--window-sec", type=int, default=300, help="Rolling window for the share")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
