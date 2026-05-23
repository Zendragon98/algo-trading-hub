"""Periodic health check for a running algo-trading-hub backend.

Usage:
    python scripts/monitor_health.py
    python scripts/monitor_health.py --api http://127.0.0.1:8000 --log-lines 500
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from socket import timeout as SocketTimeout
from collections import Counter
from pathlib import Path

_ISSUE_PATTERNS = [
    (re.compile(r"REST paused|code=-1003|binance REST: suspending", re.I), "rest_throttle"),
    (re.compile(r"flatten market failed|flatten timeout", re.I), "flatten"),
    (re.compile(r"repeat_reject|breaker tripped", re.I), "breaker"),
    (re.compile(r"reconcile_mismatch|order_reconcile_mismatch", re.I), "reconcile"),
    (re.compile(r"fetch_open_orders failed|fetch_positions failed", re.I), "rest_fetch"),
    (re.compile(r"engine paused|engine status -> paused", re.I), "paused"),
    (re.compile(r"mm2_spread_gate|mm2_skew_gate", re.I), "mm_gate"),
]

_QUOTE_RE = re.compile(r"MM quote |order placed MMQ-")


def _get(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _latest_run_log(backend_root: Path) -> Path | None:
    runs = backend_root / "data" / "runs"
    if not runs.is_dir():
        return None
    candidates = sorted(runs.glob("*/app.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Backend health monitor")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--log-lines", type=int, default=400)
    args = parser.parse_args()
    api = args.api.rstrip("/")
    backend_root = Path(__file__).resolve().parent.parent

    print("=== algo-trading-hub health ===")
    try:
        status = _get(f"{api}/api/status")
        state = _get(f"{api}/api/state")
    except (urllib.error.URLError, TimeoutError, SocketTimeout) as exc:
        print(f"API unreachable or timed out: {exc}")
        return 1

    eng_status = status.get("status", "?")
    paper = status.get("paper_mode", status.get("paper", "?"))
    uptime = status.get("uptime_sec", 0)
    print(f"engine={eng_status} paper={paper} uptime_sec={uptime:.0f}")

    snap = state if isinstance(state, dict) else {}
    equity = snap.get("equity")
    if isinstance(equity, list) and equity:
        last = equity[-1]
        print(f"equity_last={last.get('equity', last)}")
    elif isinstance(equity, (int, float)):
        print(f"equity={equity}")

    ops = snap.get("ops_health") or snap.get("system_health") or {}
    if isinstance(ops, dict):
        print(
            "ops:",
            f"tick_age={ops.get('tick_age_sec', ops.get('market_tick_age_sec', '?'))}",
            f"user_stale={ops.get('user_data_stale', '?')}",
            f"reconcile_stale={ops.get('user_data_reconcile_stale', '?')}",
        )
        breakers = ops.get("breakers") or ops.get("active_breakers")
        if breakers:
            print(f"breakers={breakers}")

    log_path = _latest_run_log(backend_root)
    if log_path is None:
        print("no run log found")
        return 0

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-max(50, args.log_lines) :]
    issues: Counter[str] = Counter()
    quotes = 0
    for line in tail:
        for pat, label in _ISSUE_PATTERNS:
            if pat.search(line):
                issues[label] += 1
        if _QUOTE_RE.search(line):
            quotes += 1

    print(f"log={log_path.name} tail_lines={len(tail)} mm_quotes_in_tail={quotes}")
    if issues:
        print("issue_counts:", dict(issues.most_common()))
    else:
        print("issue_counts: none in tail")

    mm_lines = [ln for ln in tail if " MM " in ln and ("mm2_" in ln or "bid=" in ln)]
    if mm_lines:
        print("last_mm:", mm_lines[-1][-160:])

    if eng_status != "running":
        return 2
    if quotes == 0 and issues.get("rest_throttle", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
