"""Offline stress scenarios against historical klines."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_SCENARIOS = {
    "btc_crash": {"price_scale": 0.80, "spread_scale": 1.0, "volume_scale": 1.0},
    "spread_widen": {"price_scale": 1.0, "spread_scale": 3.0, "volume_scale": 1.0},
    "volume_dryup": {"price_scale": 1.0, "spread_scale": 1.0, "volume_scale": 0.25},
}


@dataclass(slots=True)
class StressReport:
    scenario: str
    symbol: str
    bars: int
    max_drawdown_pct: float
    final_return_pct: float
    notes: list[str]


def run_scenario(
    parquet_path: Path,
    scenario: str,
    *,
    symbol: str = "BTCUSDT",
) -> StressReport:
    scales = _SCENARIOS.get(scenario, _SCENARIOS["btc_crash"])
    df = pd.read_parquet(parquet_path)
    if "close" not in df.columns:
        raise ValueError(f"missing close column in {parquet_path}")

    closes = df["close"].astype(float) * scales["price_scale"]
    if len(closes) < 2:
        return StressReport(
            scenario=scenario,
            symbol=symbol,
            bars=len(closes),
            max_drawdown_pct=0.0,
            final_return_pct=0.0,
            notes=["insufficient bars"],
        )

    peak = closes.iloc[0]
    max_dd = 0.0
    for price in closes:
        peak = max(peak, price)
        dd = (peak - price) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    ret = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] if closes.iloc[0] > 0 else 0.0
    notes: list[str] = []
    if max_dd >= 0.15:
        notes.append("hwm_drawdown_kill_pct may trip")
    if ret <= -0.05:
        notes.append("daily_loss_kill_pct may trip")

    return StressReport(
        scenario=scenario,
        symbol=symbol,
        bars=len(closes),
        max_drawdown_pct=max_dd * 100.0,
        final_return_pct=ret * 100.0,
        notes=notes,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Offline stress scenario runner")
    parser.add_argument("--scenario", default="btc_crash", choices=sorted(_SCENARIOS))
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = run_scenario(args.parquet, args.scenario)
    text = json.dumps(asdict(report), indent=2)
    print(text)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
