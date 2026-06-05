"""Aggregate netting savings metrics from run archive JSONL."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

NET_LINE = re.compile(
    r"net (BUY|SELL) (\w+) qty=([\d.eE+-]+) \((\d+) strategies\)",
    re.I,
)
NET_ZERO = re.compile(r"net zero (\w+): opposing intents cancelled", re.I)
# Strategy open lines with qty (alpha VWAP path)
STRAT_QTY = re.compile(
    r"(FLOW open|BLEND|SMA|PAIRS|net) .+? (\w+) qty=([\d.eE+-]+)",
    re.I,
)
DEFAULT_FEE_BPS = 4.0


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


@dataclass(slots=True)
class RunStats:
    run_id: str
    fill_count: int = 0
    net_notional: float = 0.0
    fees_paid: float = 0.0
    mm_fills: int = 0
    alpha_fills: int = 0
    mm_notional: float = 0.0
    alpha_notional: float = 0.0
    netted_parents: int = 0
    single_parents: int = 0
    net_events: int = 0
    multi_net_events: int = 0
    net_zero_events: int = 0
    realized_pnl_sum: float = 0.0
    fills_with_realized_pnl: int = 0


@dataclass(slots=True)
class AggregateStats:
    runs_total: int = 0
    runs_with_fills: int = 0
    runs_with_netting_logs: int = 0
    fill_count: int = 0
    fills_with_realized_pnl: int = 0
    realized_pnl_sum_usd: float = 0.0
    net_notional_usd: float = 0.0
    fees_paid_usd: float = 0.0
    mm_fills: int = 0
    alpha_fills: int = 0
    mm_notional_usd: float = 0.0
    alpha_notional_usd: float = 0.0
    netted_parents: int = 0
    single_parents: int = 0
    net_events: int = 0
    multi_net_events: int = 0
    net_zero_events: int = 0
    per_run: list[RunStats] = field(default_factory=list)

    @property
    def avg_fill_notional(self) -> float:
        return self.net_notional_usd / self.fill_count if self.fill_count else 0.0

    @property
    def fee_bps_on_notional(self) -> float:
        if self.net_notional_usd <= 0:
            return 0.0
        return (self.fees_paid_usd / self.net_notional_usd) * 10_000

    @property
    def avoided_orders_low_bound(self) -> int:
        """Each net-zero = at least one avoided venue submission."""
        return self.net_zero_events

    @property
    def estimated_avoided_notional_usd(self) -> float:
        """Proxy: net-zero events × avg fill notional (conservative)."""
        return self.net_zero_events * self.avg_fill_notional

    @property
    def estimated_fee_savings_usd(self) -> float:
        return self.estimated_avoided_notional_usd * (DEFAULT_FEE_BPS / 10_000)


def analyze_run(run_dir: Path) -> RunStats | None:
    fills_path = run_dir / "fills.jsonl"
    if not fills_path.is_file():
        return None
    stats = RunStats(run_id=run_dir.name)
    for rec in load_jsonl(fills_path):
        d = rec.get("data") or {}
        qty = float(d.get("qty") or 0)
        price = float(d.get("price") or d.get("venue_price") or 0)
        notional = abs(qty * price)
        stats.fill_count += 1
        stats.net_notional += notional
        stats.fees_paid += float(d.get("fee") or 0)
        rp = d.get("realized_pnl")
        if rp is not None:
            stats.realized_pnl_sum += float(rp)
            stats.fills_with_realized_pnl += 1
        parent_id = str(d.get("parent_id") or "")
        if parent_id.startswith("Q-"):
            stats.mm_fills += 1
            stats.mm_notional += notional
        else:
            stats.alpha_fills += 1
            stats.alpha_notional += notional

    for rec in load_jsonl(run_dir / "parents.jsonl"):
        d = rec.get("data") or {}
        sn = str(d.get("strategy_name") or "")
        if sn == "__netted__":
            stats.netted_parents += 1
        elif sn:
            stats.single_parents += 1

    for rec in load_jsonl(run_dir / "logs.jsonl"):
        msg = str((rec.get("data") or {}).get("msg") or "")
        m = NET_LINE.search(msg)
        if m:
            stats.net_events += 1
            if int(m.group(4)) > 1:
                stats.multi_net_events += 1
        if NET_ZERO.search(msg):
            stats.net_zero_events += 1

    return stats


def analyze_all(runs_root: Path) -> AggregateStats:
    agg = AggregateStats()
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir():
            continue
        agg.runs_total += 1
        run_stats = analyze_run(run_dir)
        if run_stats is None:
            continue
        agg.runs_with_fills += 1
        if run_stats.net_events or run_stats.net_zero_events:
            agg.runs_with_netting_logs += 1
        agg.fill_count += run_stats.fill_count
        agg.net_notional_usd += run_stats.net_notional
        agg.fees_paid_usd += run_stats.fees_paid
        agg.mm_fills += run_stats.mm_fills
        agg.alpha_fills += run_stats.alpha_fills
        agg.mm_notional_usd += run_stats.mm_notional
        agg.alpha_notional_usd += run_stats.alpha_notional
        agg.netted_parents += run_stats.netted_parents
        agg.single_parents += run_stats.single_parents
        agg.net_events += run_stats.net_events
        agg.multi_net_events += run_stats.multi_net_events
        agg.net_zero_events += run_stats.net_zero_events
        agg.realized_pnl_sum_usd += run_stats.realized_pnl_sum
        agg.fills_with_realized_pnl += run_stats.fills_with_realized_pnl
        agg.per_run.append(run_stats)
    return agg


def format_report(agg: AggregateStats) -> str:
    lines = [
        "# Netting analysis report",
        "",
        "## Summary (all runs with fills.jsonl)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Run folders scanned | {agg.runs_total} |",
        f"| Runs with fills | {agg.runs_with_fills} |",
        f"| Runs with netting log activity | {agg.runs_with_netting_logs} |",
        f"| Total fills | {agg.fill_count:,} |",
        f"| Net venue notional (USD) | ${agg.net_notional_usd:,.2f} |",
        f"| Fees paid (USDT) | ${agg.fees_paid_usd:,.2f} |",
        f"| Realized PnL (fills, sum) | ${agg.realized_pnl_sum_usd:,.2f} |",
        f"| Implied fee rate (bps on notional) | {agg.fee_bps_on_notional:.2f} |",
        f"| MM fills / notional | {agg.mm_fills:,} / ${agg.mm_notional_usd:,.2f} |",
        f"| Alpha (VWAP) fills / notional | {agg.alpha_fills:,} / ${agg.alpha_notional_usd:,.2f} |",
        f"| Netted parents (`__netted__`) | {agg.netted_parents:,} |",
        f"| Single-strategy parents | {agg.single_parents:,} |",
        f"| Net submit log events | {agg.net_events:,} |",
        f"| Multi-strategy net events | {agg.multi_net_events:,} |",
        f"| Full cancellations (net zero) | {agg.net_zero_events:,} |",
        "",
        "## Estimated savings (conservative)",
        "",
        f"| Estimate | Value |",
        f"|----------|-------|",
        f"| Avoided orders (net-zero events) | {agg.avoided_orders_low_bound:,} |",
        f"| Avoided notional (proxy: net-zero × avg fill) | ${agg.estimated_avoided_notional_usd:,.2f} |",
        f"| Estimated fee savings (@ {DEFAULT_FEE_BPS} bps) | ${agg.estimated_fee_savings_usd:,.2f} |",
        "",
        "**Note:** `fills.jsonl` records post-net venue execution. Gross per-strategy "
        "quantities are not persisted; multi-strategy gross-vs-net dollar savings "
        "require future `contributions` logging or log correlation.",
        "",
    ]
    top = sorted(agg.per_run, key=lambda r: r.fill_count, reverse=True)[:10]
    if top:
        lines.extend(["## Top runs by fill count", "", "| Run | Fills | Notional | Fees | Net-zero |"])
        for r in top:
            lines.append(
                f"| {r.run_id} | {r.fill_count:,} | ${r.net_notional:,.0f} | "
                f"${r.fees_paid:.2f} | {r.net_zero_events} |"
            )
        lines.append("")
    netting_runs = [r for r in agg.per_run if r.net_events or r.net_zero_events]
    if netting_runs:
        lines.extend(["## Runs with STRATEGY=all netting activity", ""])
        for r in sorted(netting_runs, key=lambda x: x.multi_net_events + x.net_zero_events, reverse=True)[:15]:
            lines.append(
                f"- **{r.run_id}**: multi-net={r.multi_net_events}, net-zero={r.net_zero_events}, "
                f"netted_parents={r.netted_parents}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "runs",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    args = parser.parse_args()
    agg = analyze_all(args.runs_dir)
    report = format_report(agg)
    print(report)
    if args.json_out:
        payload = {k: v for k, v in asdict(agg).items() if k != "per_run"}
        payload["per_run"] = [asdict(r) for r in agg.per_run]
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.md_out:
        args.md_out.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
