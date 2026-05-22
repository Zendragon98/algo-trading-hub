"""Ingest L2 depth snapshots from Binance REST into the shared library.

Samples top-of-book + depth stats on a fixed interval so spread calibration
has empirical microstructure input before MM quotes go live.

CLI:
    python -m analytics.l2_loader --symbols BTCUSDT,ETHUSDT --minutes 10 --interval-sec 1
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

from .l2_store import L2_COLS, merge_l2_snapshots

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestResult:
    symbol: str
    samples: int
    path: str


def _parse_depth_snapshot(
    raw: dict,
    symbol: str,
    *,
    top_n: int,
    ts: float,
) -> dict | None:
    bids = [(float(p), float(q)) for p, q in raw.get("bids", []) if float(q) > 0]
    asks = [(float(p), float(q)) for p, q in raw.get("asks", []) if float(q) > 0]
    if not bids or not asks:
        return None
    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    if best_bid <= 0 or best_ask <= best_bid:
        return None
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / best_bid * 10_000.0
    bids_sorted = sorted(bids, key=lambda x: -x[0])[:top_n]
    asks_sorted = sorted(asks, key=lambda x: x[0])[:top_n]
    bid_d = sum(q for _, q in bids_sorted)
    ask_d = sum(q for _, q in asks_sorted)
    denom = bid_d + ask_d
    imb = (bid_d - ask_d) / denom if denom > 0 else 0.0
    return {
        "ts": ts,
        "symbol": symbol.upper(),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "bid_depth_top_n": bid_d,
        "ask_depth_top_n": ask_d,
        "imbalance_top_n": imb,
        "last_update_id": int(raw.get("lastUpdateId", 0)),
    }


async def sample_l2(
    symbols: list[str],
    *,
    minutes: float = 10.0,
    interval_sec: float = 1.0,
    depth_limit: int = 20,
    top_n: int = 10,
    settings: Settings | None = None,
) -> list[IngestResult]:
    settings = settings or get_settings()
    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    syms = [s.strip().upper() for s in symbols if s.strip()]
    buffers: dict[str, list[dict]] = {s: [] for s in syms}
    deadline = time.time() + max(0.1, minutes) * 60.0
    interval = max(0.2, float(interval_sec))
    try:
        while time.time() < deadline:
            ts = time.time()
            for sym in syms:
                raw = await rest.book_snapshot(sym, limit=depth_limit)
                row = _parse_depth_snapshot(raw, sym, top_n=top_n, ts=ts)
                if row is not None:
                    buffers[sym].append(row)
            await asyncio.sleep(interval)
    finally:
        await rest.close()

    results: list[IngestResult] = []
    for sym, rows in buffers.items():
        df = pd.DataFrame(rows, columns=L2_COLS) if rows else pd.DataFrame(columns=L2_COLS)
        path = merge_l2_snapshots(df, sym)
        results.append(IngestResult(symbol=sym, samples=len(rows), path=str(path)))
        logger.info("L2 ingest %s: %d samples -> %s", sym, len(rows), path)
    return results


async def _run_cli(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await sample_l2(
        args.symbols,
        minutes=args.minutes,
        interval_sec=args.interval_sec,
        depth_limit=args.depth_limit,
        top_n=args.top_n,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample L2 depth into data/l2/")
    parser.add_argument(
        "--symbols",
        type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
        required=True,
    )
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--depth-limit", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
