"""Spread / cointegration analysis for a USDT-vs-USDC pair.

Given two cached kline files, computes:
    - log-spread time series
    - rolling mean + std (window configurable)
    - z-score series + entry/exit threshold suggestions (mean +/- k*std)
    - half-life of mean reversion (Ornstein-Uhlenbeck fit)
    - Engle-Granger cointegration p-value

Writes a JSON report to `data/pair_<base>.json` that the live strategy
loads at startup to set its `entry_z` / `exit_z` thresholds.

CLI:
    python -m analytics.pair_analyzer --base BTC --interval 1m --window 60
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import coint

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class PairReport:
    base: str
    usdt_symbol: str
    usdc_symbol: str
    samples: int
    spread_mean: float
    spread_std: float
    half_life_min: float | None
    coint_pvalue: float | None
    suggested_entry_z: float
    suggested_exit_z: float


def _load_close(symbol: str, interval: str) -> pd.Series:
    path = _DATA_DIR / f"klines_{symbol}_{interval}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}; run `python -m analytics.data_loader --symbols {symbol}` first"
        )
    df = pd.read_parquet(path)
    return df["close"].astype(float)


def _half_life(spread: pd.Series) -> float | None:
    """Estimate OU half-life: ds_t = -theta * s_{t-1} dt + eps."""
    if len(spread) < 60:
        return None
    s = spread.dropna()
    s_lag = s.shift(1).dropna()
    s_diff = (s - s.shift(1)).dropna()
    s_diff = s_diff.loc[s_lag.index]
    if s_lag.std() == 0:
        return None
    model = OLS(s_diff.values, add_constant(s_lag.values)).fit()
    theta = -model.params[1]
    if theta <= 0:
        return None
    return float(np.log(2) / theta)


def analyze_pair(
    base: str,
    interval: str,
    window: int,
    entry_k: float = 2.0,
    exit_k: float = 0.5,
) -> PairReport:
    usdt_symbol = f"{base}USDT"
    usdc_symbol = f"{base}USDC"
    usdt = _load_close(usdt_symbol, interval)
    usdc = _load_close(usdc_symbol, interval)

    df = pd.concat([usdt, usdc], axis=1, join="inner").dropna()
    df.columns = [usdt_symbol, usdc_symbol]
    spread = np.log(df[usdt_symbol]) - np.log(df[usdc_symbol])

    if len(spread) < window:
        raise ValueError(f"need at least {window} samples; got {len(spread)}")

    rolled = spread.rolling(window=window, min_periods=window)
    mean = rolled.mean().iloc[-1]
    std = rolled.std().iloc[-1]
    half_life = _half_life(spread)

    try:
        _t, p_value, _crit = coint(df[usdt_symbol].values, df[usdc_symbol].values)
        p_value = float(p_value)
    except Exception:  # noqa: BLE001
        p_value = None

    return PairReport(
        base=base.upper(),
        usdt_symbol=usdt_symbol,
        usdc_symbol=usdc_symbol,
        samples=int(len(spread)),
        spread_mean=float(mean),
        spread_std=float(std),
        half_life_min=half_life,
        coint_pvalue=p_value,
        suggested_entry_z=float(entry_k),
        suggested_exit_z=float(exit_k),
    )


def write_report(report: PairReport) -> Path:
    out = _DATA_DIR / f"pair_{report.base}.json"
    out.write_text(json.dumps(asdict(report), indent=2))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Pair cointegration / spread analysis")
    parser.add_argument("--base", required=True, help="Base asset, e.g. BTC")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--entry-k", type=float, default=2.0)
    parser.add_argument("--exit-k", type=float, default=0.5)
    args = parser.parse_args()

    report = analyze_pair(
        base=args.base,
        interval=args.interval,
        window=args.window,
        entry_k=args.entry_k,
        exit_k=args.exit_k,
    )
    path = write_report(report)
    logger.info("wrote %s", path)
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
