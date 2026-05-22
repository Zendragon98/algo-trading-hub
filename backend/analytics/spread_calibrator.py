"""Calibrate per-symbol MM starting spreads from ingested L2 snapshots.

Reads ``data/l2/{SYMBOL}_l2.parquet`` produced by ``l2_loader`` and writes
``data/mm_spread_calibration.json`` for the live engine to load at boot.

CLI:
    python -m analytics.spread_calibrator --symbols BTCUSDT,ETHUSDT,DOGEUSDT
    python -m analytics.spread_calibrator --symbols AUTO --from-mm-symbols
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from common.config import Settings, get_settings

from .l2_store import backend_data_root, load_l2_snapshots

logger = logging.getLogger(__name__)

DEFAULT_CALIB_PATH = backend_data_root() / "mm_spread_calibration.json"


@dataclass(slots=True)
class SymbolSpreadStats:
    symbol: str
    samples: int
    median_spread_bps: float
    p50_spread_bps: float
    p75_spread_bps: float
    p90_spread_bps: float
    median_half_spread_bps: float
    suggested_half_spread_bps: float
    suggested_min_spread_bps: float


@dataclass(slots=True)
class SpreadCalibrationReport:
    generated_at: str
    percentile: float
    half_mult: float
    buffer_bps: float
    symbols: dict[str, SymbolSpreadStats]


def calibrate_symbol_spread(
    symbol: str,
    *,
    percentile: float = 50.0,
    half_mult: float = 0.55,
    buffer_bps: float = 0.5,
    min_half_bps: float = 1.0,
    max_half_bps: float = 50.0,
    min_samples: int = 30,
) -> SymbolSpreadStats | None:
    df = load_l2_snapshots(symbol)
    if df.empty or len(df) < min_samples:
        logger.warning("%s: insufficient L2 samples (%d)", symbol, len(df))
        return None
    spreads = df["spread_bps"].astype(float)
    spreads = spreads[(spreads > 0) & (spreads < 500)]
    if len(spreads) < min_samples:
        return None
    arr = np.asarray(spreads)
    p50 = float(np.percentile(arr, 50))
    p75 = float(np.percentile(arr, 75))
    p90 = float(np.percentile(arr, 90))
    at_pct = float(np.percentile(arr, percentile))
    half = at_pct * half_mult + buffer_bps
    half = max(min_half_bps, min(max_half_bps, half))
    return SymbolSpreadStats(
        symbol=symbol.upper(),
        samples=int(len(arr)),
        median_spread_bps=float(np.median(arr)),
        p50_spread_bps=p50,
        p75_spread_bps=p75,
        p90_spread_bps=p90,
        median_half_spread_bps=p50 * 0.5,
        suggested_half_spread_bps=half,
        suggested_min_spread_bps=max(0.0, p50 * 0.85),
    )


def build_calibration(
    symbols: list[str],
    *,
    settings: Settings | None = None,
) -> SpreadCalibrationReport:
    settings = settings or get_settings()
    pct = float(getattr(settings, "mm_spread_calib_percentile", 50.0))
    half_mult = float(getattr(settings, "mm_spread_calib_half_mult", 0.55))
    buffer = float(getattr(settings, "mm_spread_calib_buffer_bps", 0.5))
    min_half = float(getattr(settings, "mm_spread_calib_min_half_bps", 1.0))
    max_half = float(getattr(settings, "mm_spread_calib_max_half_bps", 50.0))
    min_samples = int(getattr(settings, "mm_spread_calib_min_samples", 30))

    out: dict[str, SymbolSpreadStats] = {}
    for sym in symbols:
        st = calibrate_symbol_spread(
            sym,
            percentile=pct,
            half_mult=half_mult,
            buffer_bps=buffer,
            min_half_bps=min_half,
            max_half_bps=max_half,
            min_samples=min_samples,
        )
        if st is not None:
            out[st.symbol] = st

    return SpreadCalibrationReport(
        generated_at=datetime.now(UTC).isoformat(),
        percentile=pct,
        half_mult=half_mult,
        buffer_bps=buffer,
        symbols=out,
    )


def write_calibration(report: SpreadCalibrationReport, path: Path | None = None) -> Path:
    dest = path or DEFAULT_CALIB_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": report.generated_at,
        "percentile": report.percentile,
        "half_mult": report.half_mult,
        "buffer_bps": report.buffer_bps,
        "symbols": {k: asdict(v) for k, v in report.symbols.items()},
    }
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("wrote calibration %s (%d symbols)", dest, len(report.symbols))
    return dest


def _resolve_symbols(args: argparse.Namespace, settings: Settings) -> list[str]:
    if getattr(args, "from_mm_symbols", False):
        return [s.strip().upper() for s in (settings.mm_symbols or []) if s.strip()]
    syms = [s.strip().upper() for s in args.symbols if s.strip()]
    if len(syms) == 1 and syms[0] == "AUTO":
        return [s.strip().upper() for s in (settings.mm_symbols or []) if s.strip()]
    return syms


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate MM spreads from L2 library")
    parser.add_argument("--symbols", nargs="+", default=["AUTO"])
    parser.add_argument(
        "--from-mm-symbols",
        action="store_true",
        help="Use MM_SYMBOLS from settings",
    )
    parser.add_argument("--output", default=str(DEFAULT_CALIB_PATH))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    syms = _resolve_symbols(args, settings)
    report = build_calibration(syms, settings=settings)
    path = write_calibration(report, Path(args.output))
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
