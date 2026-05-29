"""MM universe refresh — keeps analytics imports out of ``engine.py``."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from common.enums import EngineStatus
from gateways.gateway_interface import GatewayInterface

from ..strategies.market_making import core as mm_core

if TYPE_CHECKING:
    from .engine import Engine

logger = logging.getLogger(__name__)


def gateway_rest_client(gateway: GatewayInterface) -> Any | None:
    """Return the venue REST client when the gateway exposes ``rest()``."""
    rest_getter = getattr(gateway, "rest", None)
    if callable(rest_getter):
        return rest_getter()
    return None


def refresh_enabled(engine: Engine) -> bool:
    s = engine.settings
    return bool(s.mm_universe_auto or s.mm2_universe_auto or s.flow_universe_auto)


def active_mm_symbols(engine: Engine) -> list[str]:
    syms: set[str] = set()
    if engine.is_multi_strategy_mode():
        targets = engine.strategies
    else:
        active = engine._strategies_by_name.get(engine.active_strategy_name)
        targets = [active] if active is not None else []
    for strat in targets:
        if strat is None or not mm_core.is_mm_strategy(strat.name):
            continue
        syms.update(strat.symbols())
    return sorted(syms)


def load_spread_baselines() -> dict[str, float]:
    try:
        from analytics.mm_universe_refresher import load_spread_baselines

        return load_spread_baselines()
    except Exception:  # noqa: BLE001
        logger.debug("mm universe spread baselines unavailable", exc_info=True)
        return {}


async def refresh_loop(engine: Engine) -> None:
    while True:
        try:
            interval = max(60.0, float(engine.settings.mm_universe_refresh_sec))
            await asyncio.sleep(interval)
            await refresh(engine, reason="periodic")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("mm universe periodic refresh failed")


async def maybe_refresh_adverse(engine: Engine) -> None:
    if not refresh_enabled(engine):
        return
    now = time.time()
    check_sec = max(5.0, float(engine.settings.mm_universe_adverse_check_sec))
    if now - engine._mm_universe_last_adverse_check_ts < check_sec:
        return
    engine._mm_universe_last_adverse_check_ts = now

    from analytics.mm_universe_refresher import (
        SymbolMicroSnapshot,
        evaluate_adverse_universe,
        should_run_adverse_refresh,
    )

    if not should_run_adverse_refresh(
        last_adverse_refresh_ts=engine._mm_universe_last_adverse_refresh_ts,
        cooldown_sec=float(engine.settings.mm_universe_adverse_refresh_cooldown_sec),
        now=now,
    ):
        return

    mm_syms = active_mm_symbols(engine)
    if not mm_syms:
        return

    snaps: dict[str, SymbolMicroSnapshot] = {}
    for sym in mm_syms:
        own = engine._sync_own_book(sym)
        pos = engine._positions.get(sym)
        pos_qty = pos.qty if pos is not None else 0.0
        feat = engine._features.snapshot(sym, own=own, position_qty=pos_qty)
        snaps[sym] = SymbolMicroSnapshot(
            markout_adverse_ewma_bps=feat.markout_adverse_ewma_bps,
            is_toxic=feat.is_toxic,
            jump_active=feat.jump_active,
            spread_bps=feat.spread_bps,
            vol_ewma_bps=feat.vol_ewma_bps,
            mid_return_1s_bps=feat.mid_return_1s_bps,
        )

    signal = evaluate_adverse_universe(
        mm_syms,
        snaps,
        settings=engine.settings,
        spread_baselines=engine._mm_universe_spread_baselines,
    )
    if signal is None:
        return
    logger.warning(
        "mm universe adverse signal: %s — %s (%s)",
        signal.reason,
        signal.detail,
        ", ".join(signal.symbols[:8]),
    )
    await refresh(engine, reason=signal.reason)


async def refresh(engine: Engine, *, reason: str) -> bool:
    if not refresh_enabled(engine):
        return False
    if engine._state.status is not EngineStatus.RUNNING:
        return False

    from analytics.mm_universe_scanner import resolve_mm_universe
    from gateways.binance.rest_client import BinanceRestClient

    settings = engine.settings
    rest = gateway_rest_client(engine._gateway)
    own_rest = rest is None
    if own_rest:
        rest = BinanceRestClient(
            base_url=settings.binance_rest_base,
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
        )
    try:
        symbols = await resolve_mm_universe(
            settings,
            rest=rest,
            force_rescan=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("mm universe refresh failed (%s)", reason)
        return False
    finally:
        if own_rest and rest is not None:
            await rest.close()

    if not symbols:
        return False

    patch: dict[str, Any] = {}
    if settings.mm_universe_auto:
        patch["mm_symbols"] = symbols
    if settings.mm2_universe_auto:
        patch["mm2_symbols"] = symbols
    if settings.flow_universe_auto:
        patch["flow_symbols"] = symbols
    if not patch:
        return False

    prev = set(active_mm_symbols(engine))
    engine.apply_settings_patch(patch)
    engine._mm_universe_spread_baselines = load_spread_baselines()
    now = time.time()
    engine._mm_universe_last_refresh_ts = now
    if reason != "periodic":
        engine._mm_universe_last_adverse_refresh_ts = now

    changed = await engine.refresh_market_universe()
    new_set = set(symbols)
    logger.info(
        "mm universe refresh (%s): %d symbols %s -> %s market_ws_changed=%s",
        reason,
        len(symbols),
        sorted(prev)[:8],
        symbols[:12],
        changed,
    )
    return changed or prev != new_set
