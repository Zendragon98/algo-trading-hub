"""One-shot: ingest L2 → calibrate per-symbol knobs (unified symbol_calibration.json).

CLI:
    python -m analytics.mm_spread_pipeline --from-mm-symbols --minutes 15
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from common.config import get_settings

from .l2_loader import sample_l2
from .symbol_calibrator import (
    build_symbol_calibration_from_l2,
    enrich_symbol_calibration_tape,
    write_symbol_calibration,
)


async def run_pipeline(
    symbols: list[str],
    *,
    minutes: float = 15.0,
    interval_sec: float = 1.0,
    enrich_tape: bool = True,
) -> None:
    settings = get_settings()
    await sample_l2(symbols, minutes=minutes, interval_sec=interval_sec, settings=settings)
    payloads = build_symbol_calibration_from_l2(symbols, settings=settings)
    if enrich_tape and payloads:
        await enrich_symbol_calibration_tape(payloads, settings)
    path = write_symbol_calibration(payloads)
    logging.getLogger(__name__).info("pipeline done -> %s", path)


def main() -> None:
    parser = argparse.ArgumentParser(description="L2 ingest + unified symbol calibration")
    parser.add_argument("--symbols", nargs="+", default=["AUTO"])
    parser.add_argument("--from-mm-symbols", action="store_true")
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--no-tape", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if args.from_mm_symbols or (len(args.symbols) == 1 and args.symbols[0].upper() == "AUTO"):
        from common.universe_bootstrap import is_auto_symbol_list

        if settings.strategy == "market_making_v2":
            raw = settings.mm2_symbols
        else:
            raw = settings.mm_symbols
        if is_auto_symbol_list(raw):
            from analytics.mm_universe_scanner import load_mm_universe_report

            report = load_mm_universe_report()
            syms = list(report.recommended) if report and report.recommended else []
            if not syms:
                from analytics.mm_universe_scanner import resolve_mm_universe

                syms = asyncio.run(resolve_mm_universe(settings, force_rescan=True))
        else:
            syms = [s.strip().upper() for s in raw if s.strip()]
    else:
        syms = [s.strip().upper() for s in args.symbols if s.strip()]
    if not syms:
        raise SystemExit("no symbols to calibrate — run mm_universe_scanner or set MM2_SYMBOLS")
    asyncio.run(
        run_pipeline(
            syms,
            minutes=args.minutes,
            interval_sec=args.interval_sec,
            enrich_tape=not args.no_tape,
        ),
    )


if __name__ == "__main__":
    main()
