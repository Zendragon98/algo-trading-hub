"""Unified per-symbol calibration from L2 snapshots and optional aggTrade tape.

Writes ``data/symbol_calibration.json`` consumed by the live engine.

CLI:
    python -m analytics.symbol_calibrator --from-mm-symbols
    python -m analytics.symbol_calibrator --symbols BTCUSDT,ETHUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from common.config import Settings, get_settings
from gateways.binance.rest_client import BinanceRestClient

from .l2_store import backend_data_root, load_l2_snapshots

logger = logging.getLogger(__name__)

DEFAULT_OUT = backend_data_root() / "symbol_calibration.json"


@dataclass(slots=True)
class SymbolCalibrationPayload:
    symbol: str
    samples: int
    mm: dict[str, float]
    execution: dict[str, float]
    risk: dict[str, float]
    fees: dict[str, float]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _calibrate_from_l2(
    symbol: str,
    settings: Settings,
    *,
    min_samples: int,
) -> SymbolCalibrationPayload | None:
    df = load_l2_snapshots(symbol)
    if df.empty or len(df) < min_samples:
        logger.warning("%s: insufficient L2 (%d)", symbol, len(df))
        return None

    spreads = df["spread_bps"].astype(float)
    spreads = spreads[(spreads > 0) & (spreads < 500)]
    imbs = df["imbalance_top_n"].astype(float).abs()
    if len(spreads) < min_samples:
        return None

    sp = np.asarray(spreads)
    imb = np.asarray(imbs)
    p50 = float(np.percentile(sp, 50))
    p75 = float(np.percentile(sp, 75))
    p90 = float(np.percentile(sp, 90))

    pct = float(settings.mm_spread_calib_percentile)
    half_mult = float(settings.mm_spread_calib_half_mult)
    buffer = float(settings.mm_spread_calib_buffer_bps)
    min_half = float(settings.mm_spread_calib_min_half_bps)
    max_half = float(settings.mm_spread_calib_max_half_bps)
    at_pct = float(np.percentile(sp, pct))
    half = _clamp(at_pct * half_mult + buffer, min_half, max_half)

    imb_p75 = float(np.percentile(imb, 75)) if len(imb) else 0.2
    imb_thresh = _clamp(imb_p75 * 0.85, 0.08, 0.45)

    jump_bps = _clamp(p90 * 1.5, 8.0, 80.0)
    inv_bps = _clamp(p75 * 0.4 + 4.0, 4.0, 30.0)
    skew_bps = float(np.std(df["mid"].pct_change().dropna()) * 10_000.0) if len(df) > 5 else 1.0
    min_skew = _clamp(skew_bps * 0.5, 0.3, 5.0)

    return SymbolCalibrationPayload(
        symbol=symbol.upper(),
        samples=int(len(sp)),
        mm={
            "half_spread_bps": half,
            "min_spread_bps": max(0.0, p50 * 0.85),
            "reservation_inventory_bps": inv_bps,
            "inventory_spread_skew_bps": _clamp(p75 * 0.25, 2.0, 15.0),
            "toxic_widen_bps": _clamp(p75 * 0.5, 2.0, 20.0),
            "depletion_widen_bps": _clamp(p75 * 0.35, 2.0, 12.0),
            "skew_scale": _clamp(1.0 / max(0.5, skew_bps), 0.3, 2.5),
            "imbalance_scale": _clamp(8.0 / max(0.1, imb_p75 * 10), 2.0, 20.0),
            "tape_scale": float(settings.mm_tape_scale),
            "depletion_scale": float(settings.mm_depletion_scale),
            "reservation_micro_weight": float(settings.mm_reservation_micro_weight),
            "jump_return_bps": jump_bps,
            "jump_vol_mult": float(settings.mm_jump_vol_mult),
            "max_adverse_markout_bps": _clamp(p75 * 0.4, 4.0, 25.0),
            "scratch_loss_bps": _clamp(p75 * 0.6, 8.0, 40.0),
            "min_exit_profit_bps": _clamp(half * 1.2 + buffer, 2.0, 30.0),
            "toxicity_threshold": float(settings.mm_toxicity_threshold),
            "depletion_pull_ratio": _clamp(0.2 + p75 / 200.0, 0.15, 0.5),
            "depletion_breaker_ratio": _clamp(0.15 + p90 / 300.0, 0.1, 0.4),
            "min_skew_bps": min_skew,
            "quote_size_pct": float(settings.mm_quote_size_pct),
            "venue_spread_mult": float(settings.mm_quote_venue_spread_mult),
        },
        execution={
            "imbalance_threshold": imb_thresh,
            "hit_ratio_threshold": 0.60,
            "spread_wide_floor_bps": max(2.0, p50 * 0.7),
            "spread_wide_ceiling_bps": _clamp(p90 * 3.0, 50.0, 500.0),
        },
        risk={
            "max_entry_spread_bps": _clamp(p90 * 2.5, 15.0, 200.0),
        },
        fees={
            "maker_fee_bps": float(settings.mm2_maker_fee_bps),
            "taker_fee_bps": float(settings.mm2_taker_fee_bps),
            "spread_buffer_bps": _clamp(half * 0.5, 1.0, 10.0),
        },
    )


async def _enrich_tape_hit_ratio(
    payloads: dict[str, SymbolCalibrationPayload],
    settings: Settings,
    *,
    lookback_min: int = 30,
) -> None:
    rest = BinanceRestClient(
        base_url=settings.binance_rest_base,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    try:
        for sym, payload in payloads.items():
            end = int(time.time() * 1000)
            start = end - lookback_min * 60_000
            trades: list[dict] = []
            cursor = start
            while cursor < end:
                page = await rest.agg_trades(symbol=sym, start_ms=cursor, end_ms=end, limit=1000)
                if not page:
                    break
                trades.extend(page)
                cursor = int(page[-1]["T"]) + 1
            if len(trades) < 50:
                continue
            from .orderbook_analyzer import _rolling_taker_buy_share

            series = _rolling_taker_buy_share(trades, 300)
            if not series:
                continue
            p80 = float(np.percentile(np.asarray(series), 80))
            payload.execution["hit_ratio_threshold"] = _clamp(p80, 0.52, 0.80)
    finally:
        await rest.close()


def build_symbol_calibration(
    symbols: list[str],
    *,
    settings: Settings | None = None,
    enrich_tape: bool = True,
) -> dict[str, SymbolCalibrationPayload]:
    settings = settings or get_settings()
    min_samples = int(settings.mm_spread_calib_min_samples)
    out: dict[str, SymbolCalibrationPayload] = {}
    for sym in symbols:
        payload = _calibrate_from_l2(sym, settings, min_samples=min_samples)
        if payload is not None:
            out[payload.symbol] = payload
    if enrich_tape and out:
        asyncio.run(_enrich_tape_hit_ratio(out, settings))
    return out


def write_symbol_calibration(
    payloads: dict[str, SymbolCalibrationPayload],
    path: Path | None = None,
) -> Path:
    dest = path or DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbols": {
            sym: {
                "samples": p.samples,
                "mm": p.mm,
                "execution": p.execution,
                "risk": p.risk,
                "fees": p.fees,
            }
            for sym, p in payloads.items()
        },
    }
    dest.write_text(json.dumps(body, indent=2), encoding="utf-8")
    legacy = backend_data_root() / "mm_spread_calibration.json"
    legacy.write_text(
        json.dumps(
            {
                "generated_at": body["generated_at"],
                "symbols": {
                    sym: {
                        "suggested_half_spread_bps": p.mm.get("half_spread_bps"),
                        "suggested_min_spread_bps": p.mm.get("min_spread_bps"),
                    }
                    for sym, p in payloads.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("wrote %s (%d symbols)", dest, len(payloads))
    return dest


def _resolve_symbols(args: argparse.Namespace, settings: Settings) -> list[str]:
    if getattr(args, "from_mm_symbols", False):
        syms = list(settings.mm_symbols or [])
    else:
        syms = list(args.symbols)
    if len(syms) == 1 and str(syms[0]).upper() == "AUTO":
        syms = list(settings.mm_symbols or [])
    return [s.strip().upper() for s in syms if s.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate all per-symbol engine knobs from L2")
    parser.add_argument("--symbols", nargs="+", default=["AUTO"])
    parser.add_argument("--from-mm-symbols", action="store_true")
    parser.add_argument("--no-tape", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    syms = _resolve_symbols(args, settings)
    payloads = build_symbol_calibration(
        syms,
        settings=settings,
        enrich_tape=not args.no_tape,
    )
    path = write_symbol_calibration(payloads, Path(args.output))
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
