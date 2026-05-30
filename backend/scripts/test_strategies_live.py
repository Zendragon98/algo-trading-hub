"""Exercise every strategy against a running backend (paper/testnet).

Usage (backend must be up on :8000):
    python scripts/test_strategies_live.py
    python scripts/test_strategies_live.py --minutes 30
    python scripts/test_strategies_live.py --minutes 2 --strategy pairs_trading_usdt_usdc

Between strategies: flatten, rearm breakers, hot-swap, then soak and scan logs.

Localhost only by default. To hit a remote VM (not recommended for soak loops):
    python scripts/test_strategies_live.py --api-url https://your-vm --allow-remote
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_API = "http://127.0.0.1:8000"
API = os.environ.get("ALGO_API_URL", DEFAULT_API).rstrip("/")
RUNS_DIR = Path(__file__).resolve().parents[1] / "data" / "runs"
DEFAULT_MINUTES = 30


def _is_local_api(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def configure_api(url: str, *, allow_remote: bool = False) -> None:
    global API
    normalized = url.rstrip("/")
    if not _is_local_api(normalized) and not allow_remote:
        raise SystemExit(
            f"Refusing remote API {normalized!r} without --allow-remote "
            "(soak scripts hot-swap strategies and flatten positions)."
        )
    API = normalized
    os.environ["ALGO_API_URL"] = normalized

STRATEGIES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        "pairs_trading_usdt_usdc",
        [
            re.compile(r"PAIRS (entry|exit)", re.I),
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
        ],
    ),
    (
        "blended_signals",
        [
            re.compile(r"BLEND (open|close)", re.I),
            re.compile(r"blend_(long|short)", re.I),
            re.compile(r"VWAP P-", re.I),
        ],
    ),
    (
        "market_making_v2",
        [
            re.compile(r"MM2 (entry|exit|tilt|open|close)", re.I),
            re.compile(r"VWAP P-", re.I),
        ],
    ),
    (
        "flow_momentum",
        [
            re.compile(r"FLOW (open|close)", re.I),
            re.compile(r"flow_momentum_", re.I),
            re.compile(r"VWAP P-", re.I),
        ],
    ),
]

# Ignore transient WS/transport noise under symbol resync during soak runs.
_BENIGN_ERROR = re.compile(
    r"websockets\.client|data transfer failed|book snapshot failed|"
    r"transport:\s*$|WinError 121|semaphore timeout|listenKey keepalive failed|"
    r"submit gated: symbol_breaker",
    re.I,
)

PROBLEM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bERROR\b"),
    re.compile(r"\bCRITICAL\b"),
    re.compile(r"Traceback \(most recent"),
    re.compile(r"engine failed"),
    re.compile(r"control \w+ failed", re.I),
    re.compile(r"keepalive ping timeout", re.I),
    re.compile(r"breaker tripped.*severity=major", re.I),
    re.compile(r"pretrade vetoed.*drawdown", re.I),
]


def _get(path: str, *, timeout: float = 120.0) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _post(path: str, body: dict | None = None, *, timeout: float = 120.0) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def _equity_pnl() -> tuple[float, float]:
    st = _get("/api/state")
    kpi = st.get("kpi") or st.get("portfolio") or {}
    equity = float(kpi.get("equity") or 0.0)
    realized = float(kpi.get("realized_pnl") or 0.0)
    return equity, realized


def _open_position_count() -> int:
    st = _get("/api/state")
    positions = st.get("positions") or []
    return sum(1 for p in positions if abs(float(p.get("qty") or 0.0)) > 1e-9)


def _flatten_settle(pause_sec: float = 6.0, flat_timeout_sec: float = 120.0) -> None:
    """Flatten, rearm breakers, resume, poll until venue-flat, then settle equity."""
    if _open_position_count() == 0:
        _post("/api/control/breakers/rearm", {})
        st = _get("/api/status")
        if st.get("status") == "paused":
            _post("/api/control/resume", timeout=60.0)
        if pause_sec > 0:
            time.sleep(pause_sec)
        _equity_pnl()
        time.sleep(2.0)
        return
    _post("/api/control/flatten", timeout=180.0)
    _post("/api/control/breakers/rearm", {})
    st = _get("/api/status")
    if st.get("status") == "paused":
        _post("/api/control/resume")

    deadline = time.time() + flat_timeout_sec
    while time.time() < deadline:
        if _open_position_count() == 0:
            break
        time.sleep(2.0)
    else:
        print(
            f"WARN: flatten timeout ({flat_timeout_sec:.0f}s) with "
            f"{_open_position_count()} open position(s)",
            flush=True,
        )

    if pause_sec > 0:
        time.sleep(pause_sec)
    # Let equity catch up after closes.
    _equity_pnl()
    time.sleep(2.0)


def _scan_problems(chunk: str) -> list[str]:
    hits: list[str] = []
    for pat in PROBLEM_PATTERNS:
        if pat.pattern == r"\bERROR\b":
            for line in chunk.splitlines():
                if " ERROR " not in line:
                    continue
                if _BENIGN_ERROR.search(line):
                    continue
                hits.append(pat.pattern)
                break
            continue
        if pat.search(chunk):
            hits.append(pat.pattern)
    return hits


def _run_strategy(
    strategy: str,
    patterns: list[re.Pattern[str]],
    wait_sec: int,
    log_path: Path | None,
    *,
    skip_flatten: bool = False,
) -> tuple[bool, str]:
    offset = log_path.stat().st_size if log_path else 0

    try:
        if not skip_flatten:
            _flatten_settle()
        else:
            _post("/api/control/breakers/rearm", {})
            st = _get("/api/status")
            if st.get("status") == "paused":
                _post("/api/control/resume")
            time.sleep(2.0)
        eq0, _ = _equity_pnl()
        _post("/api/control/strategy", {"name": strategy}, timeout=600.0)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return False, f"control failed: {exc.code} {body}"

    st = _get("/api/state")
    active = (st.get("strategy") or {}).get("name")
    print(f"\n--- {strategy} (active={active}) — soaking {wait_sec // 60}m ---", flush=True)
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(min(30.0, deadline - time.time()))
        cur = (_get("/api/state").get("strategy") or {}).get("name")
        if cur != strategy:
            print(f"  WARN: strategy drift {cur!r} -> reassert {strategy}", flush=True)
            _post("/api/control/strategy", {"name": strategy}, timeout=600.0)

    eq1, realized = _equity_pnl()
    pnl_delta = eq1 - eq0

    markers: list[str] = []
    problems: list[str] = []
    if log_path:
        chunk, _ = _log_slice_since(log_path, offset)
        for pat in patterns:
            if pat.search(chunk):
                markers.append(pat.pattern)
        problems = _scan_problems(chunk)

    ok = len(markers) > 0 and not any(
        "ERROR" in p or "Traceback" in p or "engine failed" in p for p in problems
    )
    detail = (
        f"markers={markers or 'none'}; "
        f"equity_delta={pnl_delta:+.2f}; realized={realized:+.2f}; "
        f"problems={problems or 'none'}"
    )
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Soak-test strategies on a live backend")
    parser.add_argument(
        "--minutes",
        type=float,
        default=DEFAULT_MINUTES,
        help=f"Minutes per strategy (default {DEFAULT_MINUTES})",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        dest="only",
        help="Run only this strategy (repeatable)",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ALGO_API_URL", DEFAULT_API),
        help=f"Backend base URL (default {DEFAULT_API})",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow --api-url outside localhost (will hot-swap strategies on that host)",
    )
    args = parser.parse_args()
    configure_api(args.api_url, allow_remote=args.allow_remote)
    wait_sec = max(1, int(args.minutes * 60))

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
    print(f"Engine running. Registered: {sorted(names)}", flush=True)

    log_path = _latest_log()
    if log_path is None:
        print("WARN: no app.log under data/runs — log analysis limited")

    targets = STRATEGIES
    if args.only:
        wanted = set(args.only)
        targets = [t for t in STRATEGIES if t[0] in wanted]
        missing = wanted - {t[0] for t in targets}
        if missing:
            print(f"FAIL: unknown strategy(s): {sorted(missing)}")
            return 1

    results: list[tuple[str, bool, str]] = []
    for strategy, patterns in targets:
        if strategy not in names:
            results.append((strategy, False, "not registered at boot"))
            continue
        ok, detail = _run_strategy(strategy, patterns, wait_sec, log_path)
        results.append((strategy, ok, detail))

    print("\n========== SUMMARY ==========", flush=True)
    exit_code = 0
    for name, ok, detail in results:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            exit_code = 1
        print(f"  [{tag}] {name}: {detail}", flush=True)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
