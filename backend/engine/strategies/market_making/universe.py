"""Symbol universe resolution for market-making strategies."""

from __future__ import annotations

from common.config import Settings


def resolve_mm2_symbols(settings: Settings) -> list[str]:
    """Resolve ``MM2_SYMBOLS`` (or AUTO) into a concrete symbol list."""
    configured = [s.strip().upper() for s in (settings.mm2_symbols or []) if s.strip()]
    if configured:
        if len(configured) == 1 and configured[0] == "AUTO":
            return auto_universe(settings)
        return sorted(set(configured))
    return engine_symbol_universe(settings)


def auto_universe(settings: Settings) -> list[str]:
    from analytics.mm_universe_scanner import load_mm_universe_report

    report = load_mm_universe_report()
    if report is not None and report.recommended:
        return list(report.recommended)
    return engine_symbol_universe(settings)


def engine_symbol_universe(settings: Settings) -> list[str]:
    syms = sorted({str(s).strip().upper() for s in (settings.symbols or []) if str(s).strip()})
    return syms if syms else ["BTCUSDT"]
