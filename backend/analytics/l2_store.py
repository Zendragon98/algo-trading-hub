"""Parquet library for L2 book snapshots (spread calibration input)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

L2_COLS = [
    "ts",
    "symbol",
    "best_bid",
    "best_ask",
    "mid",
    "spread_bps",
    "bid_depth_top_n",
    "ask_depth_top_n",
    "imbalance_top_n",
    "last_update_id",
]


@dataclass(slots=True)
class L2SnapshotRow:
    ts: float
    symbol: str
    best_bid: float
    best_ask: float
    mid: float
    spread_bps: float
    bid_depth_top_n: float
    ask_depth_top_n: float
    imbalance_top_n: float
    last_update_id: int


def backend_data_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def l2_library_dir() -> Path:
    path = backend_data_root() / "l2"
    path.mkdir(parents=True, exist_ok=True)
    return path


def l2_parquet_path(symbol: str) -> Path:
    return l2_library_dir() / f"{symbol.upper()}_l2.parquet"


def l2_manifest_path() -> Path:
    return l2_library_dir() / "manifest.json"


def merge_l2_snapshots(df: pd.DataFrame, symbol: str) -> Path:
    """Append/dedupe snapshots and persist under ``data/l2/``."""
    sym = symbol.upper()
    path = l2_parquet_path(sym)
    if df.empty:
        return path
    work = df[L2_COLS].copy()
    work["symbol"] = sym
    if path.exists():
        prev = pd.read_parquet(path)
        work = pd.concat([prev, work], ignore_index=True)
    work = work.drop_duplicates(subset=["ts", "symbol"], keep="last")
    work = work.sort_values("ts").reset_index(drop=True)
    work.to_parquet(path, index=False)
    _update_manifest(sym, len(work), path)
    return path


def load_l2_snapshots(symbol: str) -> pd.DataFrame:
    path = l2_parquet_path(symbol.upper())
    if not path.exists():
        return pd.DataFrame(columns=L2_COLS)
    return pd.read_parquet(path)


def _update_manifest(symbol: str, rows: int, path: Path) -> None:
    manifest_path = l2_manifest_path()
    data: dict = {}
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    symbols = data.setdefault("symbols", {})
    symbols[symbol] = {
        "rows": rows,
        "path": str(path),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
