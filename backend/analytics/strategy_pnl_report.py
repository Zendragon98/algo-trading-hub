"""Per-strategy realized PnL from run archive JSONL."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from analytics.netting_analysis import load_jsonl

MM2_STRATEGY = "market_making_v2"
RISK_EXIT = "risk_exit"
FLATTEN = "__flatten__"


@dataclass(slots=True)
class StrategyPnlRow:
    strategy: str
    close_fills: int = 0
    open_fills: int = 0
    realized_pnl_usd: float = 0.0
    attributed_pnl_usd: float = 0.0


@dataclass(slots=True)
class RunStrategyReport:
    run_id: str
    fill_count: int = 0
    close_pnl_sum: float = 0.0
    strategies: dict[str, StrategyPnlRow] = field(default_factory=dict)
    unattributed_pnl_usd: float = 0.0


@dataclass(slots=True)
class AggregateStrategyReport:
    runs_total: int = 0
    runs_with_fills: int = 0
    fill_count: int = 0
    close_pnl_sum_usd: float = 0.0
    strategies: dict[str, StrategyPnlRow] = field(default_factory=dict)
    unattributed_pnl_usd: float = 0.0
    per_run: list[RunStrategyReport] = field(default_factory=list)


def _fill_pnl(data: dict) -> float | None:
    for key in ("pnl", "realized_pnl", "rp"):
        val = data.get(key)
        if val is not None:
            return float(val)
    return None


def _parent_meta(run_dir: Path) -> dict[str, dict]:
    """parent_id -> {strategy_name, notes, strategy_contributions}."""
    meta: dict[str, dict] = {}

    for rec in load_jsonl(run_dir / "parents.jsonl"):
        data = rec.get("data") or {}
        pid = str(data.get("parent_id") or "")
        if not pid:
            continue
        entry = meta.setdefault(pid, {})
        if data.get("strategy_name"):
            entry["strategy_name"] = str(data["strategy_name"])
        if data.get("notes"):
            entry["notes"] = str(data["notes"])
        contribs = data.get("strategy_contributions")
        if isinstance(contribs, dict) and contribs:
            entry["strategy_contributions"] = {
                str(k): float(v) for k, v in contribs.items()
            }

    for rec in load_jsonl(run_dir / "executions.jsonl"):
        data = rec.get("data") or {}
        pid = str(data.get("parent_id") or "")
        if not pid:
            continue
        entry = meta.setdefault(pid, {})
        if data.get("strategy_name"):
            entry["strategy_name"] = str(data["strategy_name"])
        if data.get("notes"):
            entry["notes"] = str(data["notes"])
        contribs = data.get("strategy_contributions")
        if isinstance(contribs, dict) and contribs:
            entry["strategy_contributions"] = {
                str(k): float(v) for k, v in contribs.items()
            }

    return meta


def _resolve_strategy(
    parent_id: str,
    meta: dict[str, dict],
    notes: str,
    fill_strategy_name: str = "",
) -> str:
    if parent_id.startswith("Q-"):
        return MM2_STRATEGY
    # Operator / portfolio flatten parents carry no notes; the id prefix is
    # the only reliable tag for older archives (fix 2).
    if parent_id.startswith("P-flat-"):
        return FLATTEN
    # Prefer the strategy tag persisted on the fill row itself, then fall back
    # to the parent/execution join (fix 1). Live attribution writes both.
    info = meta.get(parent_id) or {}
    sn = str(fill_strategy_name or info.get("strategy_name") or "")
    if sn and sn != "__netted__":
        return sn
    text = notes or str(info.get("notes") or "")
    if text.startswith("risk_") or "flatten" in text.lower():
        return RISK_EXIT
    if sn == "__netted__":
        return "__netted__"
    return sn or "unknown"


def _contribution_weights(contribs: dict[str, float]) -> dict[str, float]:
    total = sum(abs(v) for v in contribs.values())
    if total <= 0:
        return {}
    return {k: abs(v) / total for k, v in contribs.items()}


def _add_pnl(rows: dict[str, StrategyPnlRow], strategy: str, pnl: float, *, is_close: bool) -> None:
    row = rows.setdefault(strategy, StrategyPnlRow(strategy=strategy))
    row.attributed_pnl_usd += pnl
    row.realized_pnl_usd += pnl
    if is_close:
        row.close_fills += 1
    else:
        row.open_fills += 1


def analyze_run(run_dir: Path) -> RunStrategyReport | None:
    fills_path = run_dir / "fills.jsonl"
    if not fills_path.is_file():
        return None

    meta = _parent_meta(run_dir)
    report = RunStrategyReport(run_id=run_dir.name)

    for rec in load_jsonl(fills_path):
        data = rec.get("data") or {}
        report.fill_count += 1
        pnl = _fill_pnl(data)
        action = str(data.get("action") or "")
        is_close = action == "close" or (pnl is not None and pnl != 0.0)

        parent_id = str(data.get("parent_id") or "")
        notes = str(data.get("notes") or data.get("reason") or "")
        fill_sn = str(data.get("strategy_name") or "")
        strategy = _resolve_strategy(parent_id, meta, notes, fill_sn)

        if pnl is None:
            if is_close:
                row = report.strategies.setdefault(strategy, StrategyPnlRow(strategy=strategy))
                row.close_fills += 1
            continue

        if is_close:
            report.close_pnl_sum += pnl

        info = meta.get(parent_id) or {}
        fill_contribs = data.get("strategy_contributions")
        contribs = (
            fill_contribs
            if isinstance(fill_contribs, dict) and fill_contribs
            else info.get("strategy_contributions") or {}
        )
        if strategy == "__netted__" and contribs:
            weights = _contribution_weights(contribs)
            if weights:
                for strat, weight in weights.items():
                    _add_pnl(report.strategies, strat, pnl * weight, is_close=is_close)
                continue

        if strategy in ("unknown", "__netted__", ""):
            report.unattributed_pnl_usd += pnl
            _add_pnl(report.strategies, strategy or "unknown", pnl, is_close=is_close)
        else:
            _add_pnl(report.strategies, strategy, pnl, is_close=is_close)

    return report


def _merge_rows(
    agg: dict[str, StrategyPnlRow],
    src: dict[str, StrategyPnlRow],
) -> None:
    for name, row in src.items():
        dst = agg.setdefault(name, StrategyPnlRow(strategy=name))
        dst.close_fills += row.close_fills
        dst.open_fills += row.open_fills
        dst.realized_pnl_usd += row.realized_pnl_usd
        dst.attributed_pnl_usd += row.attributed_pnl_usd


def analyze_all(runs_root: Path, *, run_id: str | None = None) -> AggregateStrategyReport:
    agg = AggregateStrategyReport()
    if run_id:
        run_dirs = [runs_root / run_id]
    else:
        run_dirs = sorted(runs_root.iterdir())

    for run_dir in run_dirs:
        if not run_dir.is_dir():
            continue
        agg.runs_total += 1
        run_report = analyze_run(run_dir)
        if run_report is None:
            continue
        agg.runs_with_fills += 1
        agg.fill_count += run_report.fill_count
        agg.close_pnl_sum_usd += run_report.close_pnl_sum
        agg.unattributed_pnl_usd += run_report.unattributed_pnl_usd
        _merge_rows(agg.strategies, run_report.strategies)
        agg.per_run.append(run_report)
    return agg


def format_report(agg: AggregateStrategyReport) -> str:
    lines = [
        "# Strategy PnL report",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Run folders scanned | {agg.runs_total} |",
        f"| Runs with fills | {agg.runs_with_fills} |",
        f"| Total fills | {agg.fill_count:,} |",
        f"| Close PnL sum (USD) | ${agg.close_pnl_sum_usd:,.2f} |",
        f"| Unattributed PnL (USD) | ${agg.unattributed_pnl_usd:,.2f} |",
        "",
        "## Per-strategy attributed PnL",
        "",
        "| Strategy | Close fills | Realized PnL (USD) |",
        "|----------|-------------|-------------------|",
    ]
    for name in sorted(agg.strategies.keys()):
        row = agg.strategies[name]
        lines.append(
            f"| {name} | {row.close_fills:,} | ${row.realized_pnl_usd:,.2f} |"
        )
    lines.append("")
    if agg.per_run:
        lines.extend(["## Runs", ""])
        for run in agg.per_run[:20]:
            top = sorted(run.strategies.values(), key=lambda r: -abs(r.realized_pnl_usd))[:3]
            parts = ", ".join(f"{r.strategy}=${r.realized_pnl_usd:.2f}" for r in top)
            lines.append(f"- **{run.run_id}**: fills={run.fill_count:,}, {parts}")
        lines.append("")
    lines.append(
        "**Note:** Netted parents split close PnL by `strategy_contributions` "
        "weights (|delta| share). Re-run after deploy so new parents persist contributions."
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
    parser.add_argument("--run-id", type=str, default=None, help="Single run folder name")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    args = parser.parse_args()
    agg = analyze_all(args.runs_dir, run_id=args.run_id)
    report = format_report(agg)
    print(report)
    if args.json_out:
        payload = {k: v for k, v in asdict(agg).items() if k != "per_run"}
        payload["strategies"] = {k: asdict(v) for k, v in agg.strategies.items()}
        payload["per_run"] = [
            {**asdict(r), "strategies": {k: asdict(v) for k, v in r.strategies.items()}}
            for r in agg.per_run
        ]
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.md_out:
        args.md_out.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
