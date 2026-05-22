"""Shared 1m kline library: paths, manifest, merge, and load helpers.

Live capture and bulk download both write the same parquet schema so the
backtest runner can consume either source interchangeably.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Literal

import pandas as pd

logger = logging.getLogger(__name__)

KLINE_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]

SourceKind = Literal["live", "download", "mixed"]


@dataclass(slots=True)
class DatasetInfo:
    symbol: str
    interval: str
    source: SourceKind
    rows: int
    start: str | None
    end: str | None
    path: str
    run_ids: list[str]
    updated_at: str


def backend_data_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def library_dir() -> Path:
    path = backend_data_root() / "klines"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path() -> Path:
    return library_dir() / "manifest.json"


def kline_parquet_path(symbol: str, interval: str) -> Path:
    return library_dir() / f"{symbol.upper()}_{interval}.parquet"


def legacy_parquet_path(symbol: str, interval: str) -> Path:
    """Pre-standardization flat name under ``data/``."""
    return backend_data_root() / f"klines_{symbol.upper()}_{interval}.parquet"


def run_bars_dir(run_dir: Path) -> Path:
    path = run_dir / "market_bars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_bar_path(run_dir: Path, symbol: str, interval: str) -> Path:
    return run_bars_dir(run_dir) / f"{symbol.upper()}_{interval}.parquet"


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=KLINE_COLS)
    out = df.copy()
    if "open_time" not in out.columns and out.index.name == "open_time":
        out = out.reset_index()
    if not isinstance(out["open_time"].dtype, pd.DatetimeTZDtype):
        if pd.api.types.is_numeric_dtype(out["open_time"]):
            out["open_time"] = pd.to_datetime(out["open_time"], unit="ms", utc=True)
        else:
            out["open_time"] = pd.to_datetime(out["open_time"], utc=True)
    if "close_time" in out.columns and not isinstance(out["close_time"].dtype, pd.DatetimeTZDtype):
        if pd.api.types.is_numeric_dtype(out["close_time"]):
            out["close_time"] = pd.to_datetime(out["close_time"], unit="ms", utc=True)
        else:
            out["close_time"] = pd.to_datetime(out["close_time"], utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "trades" not in out.columns:
        out["trades"] = 1
    else:
        out["trades"] = pd.to_numeric(out["trades"], errors="coerce").fillna(1).astype(int)
    if "ignore" not in out.columns:
        out["ignore"] = 0
    out["ignore"] = pd.to_numeric(out["ignore"], errors="coerce").fillna(0).astype(int)
    missing = [c for c in KLINE_COLS if c not in out.columns]
    for col in missing:
        out[col] = 0.0 if col not in ("open_time", "close_time") else pd.NaT
    out = out[KLINE_COLS].drop_duplicates(subset=["open_time"], keep="last")
    out = out.sort_values("open_time")
    return out


def _iso_range(df: pd.DataFrame) -> tuple[str | None, str | None]:
    if df.empty:
        return None, None
    start = pd.Timestamp(df["open_time"].iloc[0]).isoformat()
    end = pd.Timestamp(df["open_time"].iloc[-1]).isoformat()
    return start, end


def load_manifest() -> list[DatasetInfo]:
    path = manifest_path()
    if not path.is_file():
        return _scan_library_into_manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("failed to read manifest; rescanning library")
        return _scan_library_into_manifest()
    entries: list[DatasetInfo] = []
    for item in raw.get("datasets", []):
        try:
            entries.append(DatasetInfo(**item))
        except TypeError:
            continue
    return entries


def save_manifest(entries: list[DatasetInfo]) -> None:
    payload = {
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "datasets": [asdict(e) for e in entries],
    }
    manifest_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _scan_library_into_manifest() -> list[DatasetInfo]:
    entries: list[DatasetInfo] = []
    lib = library_dir()
    for path in sorted(lib.glob("*_*.parquet")):
        name = path.stem
        if "_" not in name:
            continue
        symbol, interval = name.rsplit("_", 1)
        try:
            df = pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            logger.warning("skipping unreadable kline parquet: %s", path)
            continue
        df = _normalize_df(df)
        start, end = _iso_range(df)
        entries.append(
            DatasetInfo(
                symbol=symbol.upper(),
                interval=interval,
                source="mixed",
                rows=len(df),
                start=start,
                end=end,
                path=str(path),
                run_ids=[],
                updated_at=datetime.now(tz=UTC).isoformat(),
            )
        )
    save_manifest(entries)
    return entries


def upsert_manifest_entry(
    symbol: str,
    interval: str,
    *,
    source: SourceKind,
    path: Path,
    run_id: str | None = None,
) -> DatasetInfo:
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        logger.warning("failed to read parquet for manifest upsert: %s", path)
        df = pd.DataFrame(columns=KLINE_COLS)
    df = _normalize_df(df)
    start, end = _iso_range(df)
    entries = load_manifest()
    key = (symbol.upper(), interval)
    existing = next((e for e in entries if (e.symbol, e.interval) == key), None)
    run_ids = list(existing.run_ids) if existing else []
    if run_id and run_id not in run_ids:
        run_ids.append(run_id)
    merged_source: SourceKind = source
    if existing and existing.source != source:
        merged_source = "mixed"
    info = DatasetInfo(
        symbol=symbol.upper(),
        interval=interval,
        source=merged_source,
        rows=len(df),
        start=start,
        end=end,
        path=str(path),
        run_ids=run_ids,
        updated_at=datetime.now(tz=UTC).isoformat(),
    )
    entries = [e for e in entries if (e.symbol, e.interval) != key]
    entries.append(info)
    save_manifest(entries)
    return info


@contextlib.contextmanager
def kline_merge_lock(
    symbol: str,
    interval: str,
    *,
    timeout_sec: float = 300.0,
) -> Iterator[None]:
    """Cross-process exclusive lock for library parquet merges."""
    lock_dir = library_dir() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{symbol.upper()}_{interval}.lock"
    deadline = time.time() + max(1.0, timeout_sec)
    fd: int | None = None
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            break
        except FileExistsError:
            time.sleep(0.05)
    else:
        raise TimeoutError(f"kline merge lock timeout for {symbol} {interval}")
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to release kline lock %s", lock_path)


def merge_into_library(
    df_new: pd.DataFrame,
    symbol: str,
    interval: str,
    *,
    source: SourceKind,
    run_id: str | None = None,
) -> Path:
    """Append bars into the shared library file, deduping by ``open_time``."""
    path = kline_parquet_path(symbol, interval)
    with kline_merge_lock(symbol, interval):
        legacy = legacy_parquet_path(symbol, interval)
        if not path.is_file() and legacy.is_file():
            legacy.rename(path)
        frames: list[pd.DataFrame] = []
        if path.is_file():
            frames.append(pd.read_parquet(path))
        if not df_new.empty:
            frames.append(_normalize_df(df_new))
        if frames:
            merged = _normalize_df(pd.concat(frames, ignore_index=True))
        else:
            merged = pd.DataFrame(columns=KLINE_COLS)
        tmp = path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(path)
    upsert_manifest_entry(symbol, interval, source=source, path=path, run_id=run_id)
    return path


def append_run_bars(
    df_new: pd.DataFrame,
    run_dir: Path,
    symbol: str,
    interval: str,
) -> Path:
    path = run_bar_path(run_dir, symbol, interval)
    frames: list[pd.DataFrame] = []
    if path.is_file():
        frames.append(pd.read_parquet(path))
    if not df_new.empty:
        frames.append(_normalize_df(df_new))
    if frames:
        merged = _normalize_df(pd.concat(frames, ignore_index=True))
    else:
        merged = pd.DataFrame(columns=KLINE_COLS)
    merged.to_parquet(path, index=False)
    return path


def load_klines(
    symbol: str,
    interval: str = "1m",
    *,
    run_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Load bars from a per-run archive or the merged library."""
    if run_dir is not None:
        path = run_bar_path(run_dir, symbol, interval)
    else:
        path = kline_parquet_path(symbol, interval)
        if not path.is_file():
            legacy = legacy_parquet_path(symbol, interval)
            if legacy.is_file():
                legacy.rename(path)
    if not path.is_file():
        return pd.DataFrame(columns=KLINE_COLS)
    df = _normalize_df(pd.read_parquet(path))
    if start is not None:
        df = df[df["open_time"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["open_time"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def load_aligned_frames(
    symbols: list[str],
    interval: str = "1m",
    *,
    run_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Return a wide frame indexed by ``open_time`` with ``{sym}_close`` columns."""
    frames: list[pd.DataFrame] = []
    for sym in symbols:
        df = load_klines(sym, interval, run_dir=run_dir, start=start, end=end)
        if df.empty:
            continue
        slim = df[["open_time", "close", "volume", "taker_buy_base", "high", "low"]].copy()
        slim = slim.rename(
            columns={
                "close": f"{sym}_close",
                "volume": f"{sym}_volume",
                "taker_buy_base": f"{sym}_taker_buy",
                "high": f"{sym}_high",
                "low": f"{sym}_low",
            }
        )
        frames.append(slim.set_index("open_time"))
    if not frames:
        return pd.DataFrame()
    wide = frames[0]
    for extra in frames[1:]:
        wide = wide.join(extra, how="outer")
    wide = wide.sort_index().ffill()
    return wide.reset_index()


def list_run_ids_with_bars(persist_base: Path) -> list[str]:
    if not persist_base.is_dir():
        return []
    out: list[str] = []
    for run in sorted(persist_base.iterdir(), reverse=True):
        if run.is_dir() and (run / "market_bars").is_dir():
            out.append(run.name)
    return out


__all__ = [
    "DatasetInfo",
    "KLINE_COLS",
    "SourceKind",
    "append_run_bars",
    "backend_data_root",
    "kline_parquet_path",
    "library_dir",
    "load_aligned_frames",
    "load_klines",
    "load_manifest",
    "merge_into_library",
    "list_run_ids_with_bars",
    "run_bar_path",
    "run_bars_dir",
    "upsert_manifest_entry",
]
