"""Run soak cycles until session equity improves by --target-usd.

Each cycle: flatten -> soak each strategy (flatten between legs) -> analyze logs -> PATCH -> repeat.
Requires backend on :8000 (python main.py --engine).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

from test_strategies_live import (  # noqa: E402
    API,
    PROBLEM_PATTERNS,
    _flatten_settle,
    _get,
    _latest_log,
    _log_slice_since,
    _run_strategy,
)
from test_strategies_live import (
    STRATEGIES as _ALL_STRATEGIES,
)

LIQUID_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
]

CYCLE_BASE_PATCH: dict[str, object] = {
    "risk_per_trade_pct": 0.001,
    "max_consecutive_losses": 20,
    "consecutive_loss_min_abs_usd": 2.0,
    "auto_rearm_consecutive_losses_after_flatten": True,
    "reconcile_heal_on_mismatch": True,
    # .env often sets PAIR_CALIBRATION_PATH=data (entry_z=2); disable for soak tuning.
    "pair_calibration_path": "",
    "symbol_calibration_path": "data/symbol_calibration.json",
}

STRATEGY_PATCHES: dict[str, dict[str, object]] = {
    "pairs_trading_usdt_usdc": {
        "pair_calibration_path": "",
        "pair_entry_z": 3.8,
        "pair_exit_z": 0.3,
        "pair_stop_z": 4.5,
        "pair_cooldown_sec": 120,
        "pair_min_hold_sec": 90,
        "pair_size_scale_cap": 1.0,
        "pair_max_new_entries_per_tick": 1,
        "pair_min_mid_price": 0.001,
    },
    "sma_crossover": {
        "sma_symbols": LIQUID_SYMBOLS,
        "sma_bar_interval_sec": 60.0,
        "sma_fast_window": 10,
        "sma_slow_window": 30,
        "sma_max_symbols": 6,
        "sma_cooldown_sec": 90,
        "sma_risk_per_trade_pct": 0.0008,
        "sma_max_entries_per_tick": 1,
        "sma_min_mid_price": 0.05,
    },
    "blended_signals": {
        "blend_symbols": ["BTCUSDT", "ETHUSDT"],
        "blend_bar_interval_sec": 300.0,
        "blend_entry_threshold": 0.35,
        "blend_min_confirming_votes": 3,
        "blend_cooldown_sec": 120.0,
        "blend_risk_per_trade_pct": 0.0008,
        "blend_max_entries_per_tick": 1,
    },
    "market_making": {
        "mm_symbols": LIQUID_SYMBOLS,
        "mm_quote_half_spread_bps": 4.0,
        "mm_symbol_half_spread_bps": {},
        "mm_quote_use_venue_spread_floor": True,
        "mm_skew_window_sec": 300.0,
        "mm_cooldown_sec": 60.0,
        "mm_max_entries_per_tick": 1,
        "mm_risk_per_trade_pct": 0.0008,
        "mm_min_tape_trades": 5,
        "mm_min_samples": 8,
    },
    "market_making_v2": {
        "mm2_symbols": ["BTCUSDT", "ETHUSDT"],
        "mm2_min_spread_bps": 6.0,
        "mm2_min_skew_bps": 0.5,
        "mm2_tape_confirm": 0.0,
        "mm2_min_exit_profit_bps": 10.0,
        "mm2_max_hold_sec": 90.0,
        "mm2_cooldown_sec": 45.0,
        "mm2_max_entries_per_tick": 1,
        "mm2_risk_per_trade_pct": 0.0004,
        "mm2_min_samples": 5,
    },
}

_MARKER_COUNTS: dict[str, re.Pattern[str]] = {
    "pairs_trading_usdt_usdc": re.compile(r"PAIRS (entry|exit)", re.I),
    "sma_crossover": re.compile(r"SMA (open|close)", re.I),
    "blended_signals": re.compile(r"BLEND (open|close)", re.I),
    "market_making": re.compile(r"MM .* venue=", re.I),
    "market_making_v2": re.compile(r"mm2_|MM .* venue=", re.I),
}

# MM2 + MM + SMA only — pairs (74 symbols) dominates flatten slippage in soak cycles.
STRATEGY_ORDER = [
    "market_making_v2",
]

_IGNORE_ERROR_SUBSTR = (
    "websockets.client",
    "data transfer failed",
    "book snapshot failed",
    "transport:",
    "WinError 121",
    "semaphore timeout",
    "listenKey keepalive failed",
    "submit gated: symbol_breaker",
)


def _equity() -> float:
    kpi = (_get("/api/state").get("kpi") or {})
    return float(kpi.get("equity") or 0.0)


def _patch_settings(patch: dict[str, object]) -> None:
    data = json.dumps(patch).encode()
    req = urllib.request.Request(
        f"{API}/api/settings",
        data=data,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def _prepare_cycle() -> None:
    print("  cycle flatten + rearm...", flush=True)
    _flatten_settle(pause_sec=8.0, flat_timeout_sec=150.0)
    try:
        _patch_settings(CYCLE_BASE_PATCH)
    except urllib.error.HTTPError as exc:
        print(f"  base PATCH failed: {exc.read().decode(errors='replace')[:200]}", flush=True)


def _apply_strategy_patch(strategy: str) -> None:
    patch = STRATEGY_PATCHES.get(strategy)
    if not patch:
        return
    try:
        _patch_settings(patch)
    except urllib.error.HTTPError as exc:
        print(f"  {strategy} PATCH failed: {exc.read().decode(errors='replace')[:200]}", flush=True)


def _scan_actionable_errors(chunk: str) -> list[str]:
    lines = []
    for line in chunk.splitlines():
        if " ERROR " not in line and "Traceback" not in line:
            continue
        if any(s in line for s in _IGNORE_ERROR_SUBSTR):
            continue
        lines.append(line.strip()[:240])
    return lines


def _parse_equity_delta(detail: str) -> float:
    m = re.search(r"equity_delta=([+-]?\d+\.?\d*)", detail)
    return float(m.group(1)) if m else 0.0


def _count_markers(chunk: str, strategy: str) -> int:
    pat = _MARKER_COUNTS.get(strategy)
    if pat is None:
        return 0
    return len(pat.findall(chunk))


def _analyze_cycle(
    chunk: str,
    results: list[tuple[str, bool, str]],
) -> None:
    print("  --- analysis ---", flush=True)
    for name, ok, detail in results:
        leg_delta = _parse_equity_delta(detail)
        n = _count_markers(chunk, name)
        flag = "PASS" if ok else "FAIL"
        print(
            f"    {flag} {name}: delta={leg_delta:+.2f} signals={n} | {detail}",
            flush=True,
        )
        if leg_delta < -10 and n == 0:
            print(
                f"      note: large loss with no signals (likely flatten/slippage, not {name})",
                flush=True,
            )


def _tune_from_cycle(
    results: list[tuple[str, bool, str]],
    chunk: str,
    errors: list[str],
    cycle_delta: float,
    cycle: int,
) -> dict[str, object]:
    patch: dict[str, object] = {"pair_calibration_path": ""}

    for name, _ok, detail in results:
        leg_delta = _parse_equity_delta(detail)
        n = _count_markers(chunk, name)
        sp = STRATEGY_PATCHES.get(name)
        if sp is None:
            continue
        if leg_delta < -15 and n == 0 and name == "pairs_trading_usdt_usdc":
            sp["pair_entry_z"] = min(5.0, float(sp.get("pair_entry_z", 3.8)) + 0.2)
            patch["pair_entry_z"] = sp["pair_entry_z"]
        if n == 0:
            continue
        if leg_delta >= -5:
            continue
        if name == "pairs_trading_usdt_usdc":
            sp["pair_entry_z"] = min(5.0, float(sp.get("pair_entry_z", 3.8)) + 0.15)
            sp["pair_cooldown_sec"] = min(240, int(sp.get("pair_cooldown_sec", 120)) + 15)
            patch["pair_entry_z"] = sp["pair_entry_z"]
            patch["pair_cooldown_sec"] = sp["pair_cooldown_sec"]
        elif name == "sma_crossover":
            sp["sma_cooldown_sec"] = min(180, int(sp.get("sma_cooldown_sec", 90)) + 15)
            sp["sma_risk_per_trade_pct"] = max(0.0004, float(sp.get("sma_risk_per_trade_pct", 0.0008)) - 0.0001)
            patch["sma_cooldown_sec"] = sp["sma_cooldown_sec"]
            patch["sma_risk_per_trade_pct"] = sp["sma_risk_per_trade_pct"]
        elif name == "market_making":
            sp["mm_quote_half_spread_bps"] = min(
                8.0, float(sp.get("mm_quote_half_spread_bps", 4.0)) + 0.25,
            )
            sp["mm_cooldown_sec"] = min(120.0, float(sp.get("mm_cooldown_sec", 60.0)) + 10.0)
            sp["mm_risk_per_trade_pct"] = max(0.0004, float(sp.get("mm_risk_per_trade_pct", 0.0008)) - 0.0001)
            patch["mm_quote_half_spread_bps"] = sp["mm_quote_half_spread_bps"]
            patch["mm_cooldown_sec"] = sp["mm_cooldown_sec"]
            patch["mm_risk_per_trade_pct"] = sp["mm_risk_per_trade_pct"]
        elif name == "market_making_v2":
            sp["mm2_min_spread_bps"] = min(12.0, float(sp.get("mm2_min_spread_bps", 6.0)) + 0.5)
            sp["mm2_min_exit_profit_bps"] = min(12.0, float(sp.get("mm2_min_exit_profit_bps", 10.0)) + 0.5)
            sp["mm2_risk_per_trade_pct"] = max(0.0004, float(sp.get("mm2_risk_per_trade_pct", 0.0004)) - 0.0001)
            patch["mm2_min_spread_bps"] = sp["mm2_min_spread_bps"]
            patch["mm2_min_exit_profit_bps"] = sp["mm2_min_exit_profit_bps"]
            patch["mm2_risk_per_trade_pct"] = sp["mm2_risk_per_trade_pct"]

    if cycle_delta < -15:
        patch["risk_per_trade_pct"] = max(0.0005, 0.001 - 0.0002 * cycle)

    if any("reconcile mismatch" in e for e in errors):
        patch["reconcile_interval_sec"] = min(300, 120 + 30 * cycle)

    if any("severity=major" in e for e in errors):
        patch["max_consecutive_losses"] = min(25, 20 + cycle)

    return patch


def main() -> int:
    parser = argparse.ArgumentParser(description="Soak until equity target met")
    parser.add_argument("--minutes", type=float, default=15.0, help="Minutes per strategy leg")
    parser.add_argument("--target-usd", type=float, default=50.0, help="Stop when equity >= start + this")
    parser.add_argument(
        "--consecutive-profit-cycles",
        type=int,
        default=2,
        help="Stop after this many consecutive cycles with cycle PnL > 0 (0 = disabled)",
    )
    parser.add_argument("--max-cycles", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--strategies", nargs="*", default=[], help="Subset (default: all four)")
    args = parser.parse_args()
    wait_sec = max(60, int(args.minutes * 60))

    try:
        if _get("/api/status").get("status") != "running":
            print("FAIL: engine not running — start: python main.py --engine")
            return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL: backend unreachable: {exc}")
        return 1

    start_equity = _equity()
    target = start_equity + args.target_usd
    print(
        f"Profit loop: start_equity={start_equity:.2f} target={target:.2f} "
        f"({args.minutes:.0f}m/strategy, per-cycle flatten)",
        flush=True,
    )

    names = {s["name"] for s in _get("/api/state").get("strategies", [])}
    by_name = {t[0]: t for t in _ALL_STRATEGIES}
    targets = [by_name[n] for n in STRATEGY_ORDER if n in by_name]
    if args.strategies:
        wanted = set(args.strategies)
        targets = [by_name[n] for n in STRATEGY_ORDER if n in wanted and n in by_name]

    log_path = _latest_log()
    cycle = 0
    consecutive_profitable = 0

    while True:
        cycle += 1
        if args.max_cycles and cycle > args.max_cycles:
            print(f"Stopped: max_cycles={args.max_cycles}")
            return 1

        cycle_start = _equity()
        log_offset = log_path.stat().st_size if log_path else 0

        print(f"\n========== CYCLE {cycle} (equity={cycle_start:.2f}) ==========", flush=True)
        _prepare_cycle()

        results: list[tuple[str, bool, str]] = []
        first_leg = True
        for strategy, patterns in targets:
            if strategy not in names:
                results.append((strategy, False, "not registered"))
                continue
            _apply_strategy_patch(strategy)
            try:
                ok, detail = _run_strategy(
                    strategy,
                    patterns,
                    wait_sec,
                    log_path,
                    skip_flatten=first_leg,
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                ok, detail = False, f"leg error: {exc}"
            first_leg = False
            results.append((strategy, ok, detail))
            print(f"  [{('PASS' if ok else 'FAIL')}] {strategy}: {detail}", flush=True)

        cycle_end = _equity()
        cycle_delta = cycle_end - cycle_start
        total_delta = cycle_end - start_equity

        chunk = ""
        if log_path:
            chunk, log_offset = _log_slice_since(log_path, log_offset)
        errors = _scan_actionable_errors(chunk)
        majors = [p.pattern for p in PROBLEM_PATTERNS if p.search(chunk) and "major" in p.pattern]

        _analyze_cycle(chunk, results)

        print(
            f"Cycle {cycle} PnL: {cycle_delta:+.2f} | total: {total_delta:+.2f} | "
            f"errors={len(errors)} majors={len(majors)}",
            flush=True,
        )

        patch = _tune_from_cycle(results, chunk, errors + majors, cycle_delta, cycle)
        if patch:
            print(f"  fix PATCH: {patch}", flush=True)
            try:
                _patch_settings(patch)
            except urllib.error.HTTPError as exc:
                print(f"  PATCH failed: {exc.read().decode(errors='replace')[:200]}")

        if cycle_delta > 0:
            consecutive_profitable += 1
            print(
                f"  profitable cycle streak: {consecutive_profitable}"
                f"/{args.consecutive_profit_cycles or 'off'}",
                flush=True,
            )
        else:
            consecutive_profitable = 0

        if args.consecutive_profit_cycles and consecutive_profitable >= args.consecutive_profit_cycles:
            print(
                f"\nCONSECUTIVE PROFIT CYCLES MET: {consecutive_profitable} cycles "
                f"(last cycle {cycle_delta:+.2f}, total {total_delta:+.2f})",
                flush=True,
            )
            return 0

        if cycle_end >= target:
            print(
                f"\nPROFIT TARGET MET: equity {cycle_end:.2f} >= {target:.2f} (+{total_delta:.2f})",
                flush=True,
            )
            return 0

        if errors:
            print("  sample errors:", flush=True)
            for line in errors[:5]:
                print(f"    {line}", flush=True)

        time.sleep(3)

    return 1


if __name__ == "__main__":
    sys.exit(main())
