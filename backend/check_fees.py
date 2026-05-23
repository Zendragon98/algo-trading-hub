"""Binance futures fee audit — commission tier + recent fill commissions."""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BASE = os.environ.get("CHECK_FEES_BASE", "https://fapi.binance.com")


def signed(params: dict) -> dict:
    qs = urlencode(params)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params


def main() -> int:
    if not API_KEY or not API_SECRET:
        print("BINANCE_API_KEY and BINANCE_API_SECRET must be set", file=sys.stderr)
        return 1

    headers = {"X-MBX-APIKEY": API_KEY}
    print(f"BASE={BASE}\n")

    r = requests.get(
        f"{BASE}/fapi/v1/commissionRate",
        params=signed({"symbol": "BTCUSDT", "timestamp": int(time.time() * 1000)}),
        headers=headers,
        timeout=30,
    )
    print("=== Commission Rate (BTCUSDT) ===")
    print(r.json())

    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        r = requests.get(
            f"{BASE}/fapi/v1/userTrades",
            params=signed(
                {
                    "symbol": sym,
                    "limit": 20,
                    "timestamp": int(time.time() * 1000),
                }
            ),
            headers=headers,
            timeout=30,
        )
        trades = r.json()
        if not trades or not isinstance(trades, list):
            print(f"\n{sym}: no trades or error:", trades)
            continue
        print(f"\n=== {sym} — last {len(trades)} trades ===")
        for t in trades[-5:]:
            qty = float(t["qty"])
            price = float(t["price"])
            notional = qty * price
            comm = float(t["commission"])
            comm_asset = t["commissionAsset"]
            maker = t["maker"]
            side = t["side"]
            comm_bps = (comm / notional * 10000) if notional > 0 else 0
            print(
                f"  {side:4s} maker={maker!s:5s} notional=${notional:,.2f} "
                f"commission={comm:.6f} {comm_asset} ({comm_bps:+.3f} bps)"
            )

    r = requests.get(
        f"{BASE}/fapi/v2/account",
        params=signed({"timestamp": int(time.time() * 1000)}),
        headers=headers,
        timeout=30,
    )
    acct = r.json()
    print("\n=== Account ===")
    print("feeTier:", acct.get("feeTier"))
    print("canTrade:", acct.get("canTrade"))
    for a in acct.get("assets", []):
        if a["asset"] in ("USDT", "USDC", "BNB"):
            print(
                f"  {a['asset']}: wallet={a['walletBalance']} "
                f"unrealised={a['unrealizedProfit']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
