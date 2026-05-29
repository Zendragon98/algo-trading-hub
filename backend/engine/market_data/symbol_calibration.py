"""Load per-symbol calibration artefacts produced by analytics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_cache_path: str = ""
_cache_mtime: float = 0.0
_cache_symbols: dict[str, dict[str, float]] = {}


@dataclass(frozen=True, slots=True)
class SymbolCalibration:
    """Calibrated knobs for one symbol (unset fields use Settings defaults)."""

    symbol: str
    half_spread_bps: float | None = None
    min_spread_bps: float | None = None
    reservation_inventory_bps: float | None = None
    inventory_spread_skew_bps: float | None = None
    toxic_widen_bps: float | None = None
    depletion_widen_bps: float | None = None
    quote_size_pct: float | None = None
    venue_spread_mult: float | None = None
    skew_scale: float | None = None
    imbalance_scale: float | None = None
    tape_scale: float | None = None
    depletion_scale: float | None = None
    reservation_micro_weight: float | None = None
    jump_return_bps: float | None = None
    jump_vol_mult: float | None = None
    max_adverse_markout_bps: float | None = None
    scratch_loss_bps: float | None = None
    min_exit_profit_bps: float | None = None
    toxicity_threshold: float | None = None
    depletion_pull_ratio: float | None = None
    depletion_breaker_ratio: float | None = None
    min_skew_bps: float | None = None
    maker_fee_bps: float | None = None
    taker_fee_bps: float | None = None
    spread_buffer_bps: float | None = None
    imbalance_threshold: float | None = None
    hit_ratio_threshold: float | None = None
    spread_wide_floor_bps: float | None = None
    spread_wide_ceiling_bps: float | None = None
    max_entry_spread_bps: float | None = None
    pair_entry_z: float | None = None
    pair_exit_z: float | None = None
    pair_stop_z: float | None = None


_FLOAT_KEYS = frozenset({
    "half_spread_bps",
    "suggested_half_spread_bps",
    "min_spread_bps",
    "suggested_min_spread_bps",
    "reservation_inventory_bps",
    "inventory_spread_skew_bps",
    "toxic_widen_bps",
    "depletion_widen_bps",
    "quote_size_pct",
    "size_pct",
    "venue_spread_mult",
    "skew_scale",
    "imbalance_scale",
    "tape_scale",
    "depletion_scale",
    "reservation_micro_weight",
    "mm_reservation_micro_weight",
    "jump_return_bps",
    "jump_vol_mult",
    "max_adverse_markout_bps",
    "scratch_loss_bps",
    "min_exit_profit_bps",
    "toxicity_threshold",
    "depletion_pull_ratio",
    "depletion_breaker_ratio",
    "min_skew_bps",
    "maker_fee_bps",
    "taker_fee_bps",
    "mm2_maker_fee_bps",
    "mm2_taker_fee_bps",
    "spread_buffer_bps",
    "mm2_spread_buffer_bps",
    "imbalance_threshold",
    "hit_ratio_threshold",
    "suggested_hit_ratio_threshold",
    "spread_wide_floor_bps",
    "spread_wide_ceiling_bps",
    "max_entry_spread_bps",
    "pair_entry_z",
    "suggested_entry_z",
    "pair_exit_z",
    "suggested_exit_z",
    "pair_stop_z",
    "suggested_stop_z",
})


def _backend_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data"


def _resolve_data_path(path_str: str) -> Path:
    """Resolve a settings path under ``backend/data/`` (accepts optional ``data/`` prefix)."""
    p = Path(path_str.strip())
    if p.is_absolute():
        return p
    rel = p.as_posix().lstrip("/")
    if rel.startswith("data/"):
        rel = rel[5:]
    return _backend_data_dir() / rel


_warned_missing: set[str] = set()


def default_calibration_path() -> Path:
    return _backend_data_dir() / "symbol_calibration.json"


def _parse_symbol_fields(raw: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for section in ("mm", "execution", "risk", "fees", "pairs"):
        block = raw.get(section)
        if isinstance(block, dict):
            for k, v in block.items():
                if k in _FLOAT_KEYS or isinstance(v, (int, float)):
                    out[str(k)] = float(v)
    for k, v in raw.items():
        if k in ("mm", "execution", "risk", "fees", "pairs", "symbol", "samples"):
            continue
        if k in _FLOAT_KEYS and isinstance(v, (int, float)):
            out[str(k)] = float(v)
    if "suggested_half_spread_bps" in out and "half_spread_bps" not in out:
        out["half_spread_bps"] = out["suggested_half_spread_bps"]
    if "suggested_min_spread_bps" in out and "min_spread_bps" not in out:
        out["min_spread_bps"] = out["suggested_min_spread_bps"]
    if "suggested_hit_ratio_threshold" in out and "hit_ratio_threshold" not in out:
        out["hit_ratio_threshold"] = out["suggested_hit_ratio_threshold"]
    return out


def _entry_from_flat(sym: str, flat: dict[str, float]) -> SymbolCalibration:
    def g(*keys: str) -> float | None:
        for key in keys:
            if key in flat:
                return float(flat[key])
        return None

    return SymbolCalibration(
        symbol=sym,
        half_spread_bps=g("half_spread_bps", "suggested_half_spread_bps"),
        min_spread_bps=g("min_spread_bps", "suggested_min_spread_bps"),
        reservation_inventory_bps=g("reservation_inventory_bps"),
        inventory_spread_skew_bps=g("inventory_spread_skew_bps"),
        toxic_widen_bps=g("toxic_widen_bps"),
        depletion_widen_bps=g("depletion_widen_bps"),
        quote_size_pct=g("quote_size_pct", "size_pct"),
        venue_spread_mult=g("venue_spread_mult"),
        skew_scale=g("skew_scale"),
        imbalance_scale=g("imbalance_scale"),
        tape_scale=g("tape_scale"),
        depletion_scale=g("depletion_scale"),
        reservation_micro_weight=g(
            "reservation_micro_weight", "mm_reservation_micro_weight"
        ),
        jump_return_bps=g("jump_return_bps"),
        jump_vol_mult=g("jump_vol_mult"),
        max_adverse_markout_bps=g("max_adverse_markout_bps"),
        scratch_loss_bps=g("scratch_loss_bps"),
        min_exit_profit_bps=g("min_exit_profit_bps"),
        toxicity_threshold=g("toxicity_threshold"),
        depletion_pull_ratio=g("depletion_pull_ratio"),
        depletion_breaker_ratio=g("depletion_breaker_ratio"),
        min_skew_bps=g("min_skew_bps"),
        maker_fee_bps=g("maker_fee_bps", "mm2_maker_fee_bps"),
        taker_fee_bps=g("taker_fee_bps", "mm2_taker_fee_bps"),
        spread_buffer_bps=g("spread_buffer_bps", "mm2_spread_buffer_bps"),
        imbalance_threshold=g("imbalance_threshold"),
        hit_ratio_threshold=g("hit_ratio_threshold", "suggested_hit_ratio_threshold"),
        spread_wide_floor_bps=g("spread_wide_floor_bps"),
        spread_wide_ceiling_bps=g("spread_wide_ceiling_bps"),
        max_entry_spread_bps=g("max_entry_spread_bps"),
        pair_entry_z=g("pair_entry_z", "suggested_entry_z"),
        pair_exit_z=g("pair_exit_z", "suggested_exit_z"),
        pair_stop_z=g("pair_stop_z", "suggested_stop_z"),
    )


def _load_json_file(path: Path) -> dict[str, dict[str, float]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("symbol calibration unreadable %s: %s", path, exc)
        return {}
    symbols_raw = raw.get("symbols") or {}
    parsed: dict[str, dict[str, float]] = {}
    if not isinstance(symbols_raw, dict):
        return parsed
    for sym_key, fields in symbols_raw.items():
        sym = str(sym_key).strip().upper()
        if not sym or not isinstance(fields, dict):
            continue
        parsed[sym] = _parse_symbol_fields(fields)
    return parsed


def load_symbol_calibration(path: str | None = None) -> dict[str, SymbolCalibration]:
    """Load calibration; merges legacy ``mm_spread_calibration.json`` if needed."""
    global _cache_path, _cache_mtime, _cache_symbols

    paths: list[Path] = []
    path_str = (path or "").strip()
    primary: Path | None = None
    if path_str:
        primary = _resolve_data_path(path_str)
    legacy = _backend_data_dir() / "mm_spread_calibration.json"
    # Legacy spread file first; explicit primary path wins on key conflicts.
    if legacy.exists() and legacy != primary:
        paths.append(legacy)
    if primary is not None and primary.exists():
        paths.append(primary)

    if not paths:
        if path_str:
            wanted = _resolve_data_path(path_str)
            key = str(wanted)
            if key not in _warned_missing:
                _warned_missing.add(key)
                logger.warning(
                    "symbol calibration missing at %s — MM jump/spread/fees use Settings "
                    "defaults; run: python -m analytics.mm_spread_pipeline --from-mm-symbols",
                    wanted,
                )
        return {}

    mtime = max(p.stat().st_mtime for p in paths)
    cache_key = "|".join(str(p) for p in paths)
    if cache_key == _cache_path and mtime == _cache_mtime and _cache_symbols:
        return {sym: _entry_from_flat(sym, flat) for sym, flat in _cache_symbols.items()}

    merged: dict[str, dict[str, float]] = {}
    for p in paths:
        for sym, flat in _load_json_file(p).items():
            merged.setdefault(sym, {}).update(flat)

    _cache_path = cache_key
    _cache_mtime = mtime
    _cache_symbols = merged
    logger.info("loaded symbol calibration: %d symbols", len(merged))
    return {sym: _entry_from_flat(sym, flat) for sym, flat in merged.items()}


def pick(cal: SymbolCalibration | None, value: float | None, default: float) -> float:
    return float(value) if value is not None else default


def invalidate_cache() -> None:
    global _cache_path, _cache_mtime, _cache_symbols
    _cache_path = ""
    _cache_mtime = 0.0
    _cache_symbols = {}
