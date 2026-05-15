"""Exercise all three strategies against a running backend (paper/testnet).

Usage (backend must be up on :8000):
    python scripts/test_strategies_live.py

Checks each strategy for ~45s via POST /api/control/strategy and scans
the latest run ``app.log`` for strategy-specific activity markers.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8000"
RUNS_DIR = Path(__file__).resolve().parents[1] / "data" / "runs"
WAIT_SEC = 45

STRATEGIES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        "pairs_trading_usdt_usdc",
        [
            re.compile(r"PAIRS entry", re.I),
            re.compile(r"group .+ submitting", re.I),
            re.compile(r"VWAP P-", re.I),
        ],
    ),
    (
        "sma_crossover",
        [
            re.compile(r"SMA (open|close)", re.I),
            re.compile(r"sma_cross_", re.I),
            re.compile(r"VWAP P-", re.I),
            re.compile(r"pretrade vetoed", re.I),
        ],
    ),
    (
        "market_making",
        [
            re.compile(r"MM (tilt|open|close)", re.I),
            re.compile(r"VWAP P-", re.I),
            re.compile(r"pretrade vetoed", re.I),
        ],
    ),
]


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def _post_strategy(name: str) -> dict:
    body = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{API}/api/control/strategy",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _latest_log() -> Path | None:
    if not RUNS_DIR.is_dir():
        return None
    runs = sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        log = run / "app.log"
        if log.is_file():
            return log
    return None


def _log_slice_since(path: Path, start_offset: int) -> tuple[str, int]:
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(start_offset)
        return f.read(), size


def main() -> int:
    try:
        status = _get("/api/status")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL: backend not reachable at {API}: {exc}")
        return 1

    if status.get("status") != "running":
        print(f"FAIL: engine status={status.get('status')!r} (expected 'running')")
        print("  Start with: python main.py --engine")
        return 1

    state = _get("/api/state")
    names = {s["name"] for s in state.get("strategies", [])}
    print(f"Engine running (paper={status.get('paper_mode')}). Registered: {sorted(names)}")

    log_path = _latest_log()
    if log_path is None:
        print("WARN: no app.log under data/runs — log markers will not be checked")

    results: list[tuple[str, bool, str]] = []
    for strategy, patterns in STRATEGIES:
        if strategy not in names:
            results.append((strategy, False, "not registered at boot"))
            continue

        offset = log_path.stat().st_size if log_path else 0
        try:
            _post_strategy(strategy)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            results.append((strategy, False, f"POST strategy failed: {exc.code} {body}"))
            continue

        st = _get("/api/state")
        active = st.get("strategy", {}).get("name")
        print(f"\n--- {strategy} (active={active}) — waiting {WAIT_SEC}s ---")
        time.sleep(WAIT_SEC)

        hits: list[str] = []
        if log_path:
            chunk, _ = _log_slice_since(log_path, offset)
            for pat in patterns:
                if pat.search(chunk):
                    hits.append(pat.pattern)

        ok = len(hits) > 0
        detail = f"log markers: {hits}" if hits else "no activity markers in log slice"
        results.append((strategy, ok, detail))

    print("\n========== SUMMARY ==========")
    exit_code = 0
    for name, ok, detail in results:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            exit_code = 1
        print(f"  [{tag}] {name}: {detail}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
