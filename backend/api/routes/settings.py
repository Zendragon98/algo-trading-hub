"""GET/PATCH /api/settings — read and update runtime ``Settings``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from common.breaker_registry import merge_breaker_enabled
from common.config import Settings
from common.config.aliases import normalize_strategy_name
from common.enums import EngineStatus
from common.universe_bootstrap import needs_auto_universe_resolve, resolve_binance_auto_universe
from engine.core.engine import Engine

from ..breaker_patch import assert_live_major_disable_allowed
from ..dependencies import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_SECRET_KEYS = frozenset({"binance_api_key", "binance_api_secret"})


def _mask_secrets(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for k in _SECRET_KEYS:
        if out.get(k):
            out[k] = "***"
    return out


@router.get("")
def get_settings(engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    raw = engine.settings.model_dump(mode="json")
    return {"settings": _mask_secrets(raw)}


@router.patch("")
async def patch_settings(
    patch: dict[str, Any] = Body(...),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Merge JSON fields into the engine's live ``Settings``.

    Omitted or placeholder secrets (``\"\"``, ``\"***\"``) keep existing keys.
    Values apply immediately where wired through ``Engine._apply_runtime_settings``;
    ``api_host`` / ``api_port`` need an API restart to change bind address.
    """
    try:
        clean = dict(patch)
        confirm_live_disable = bool(clean.pop("confirm_live_disable", False))
        confirm_token = str(clean.pop("confirm_token", ""))
        be_patch = clean.get("breaker_enabled")
        if isinstance(be_patch, dict):
            partial = {str(k): bool(v) for k, v in be_patch.items()}
            assert_live_major_disable_allowed(
                engine.settings,
                partial,
                confirm_live_disable=confirm_live_disable,
                confirm_token=confirm_token,
            )
            clean["breaker_enabled"] = merge_breaker_enabled(
                partial,
                base=engine.settings.breaker_enabled,
            )
        merged = {**engine.settings.model_dump(mode="json"), **clean}
        probe = Settings.model_validate(merged)
        if needs_auto_universe_resolve(probe):
            expanded = await resolve_binance_auto_universe(probe)
            for key in (
                "symbols",
                "sma_symbols",
                "blend_symbols",
                "flow_symbols",
                "mm_symbols",
                "mm2_symbols",
                "mm_universe_auto",
                "mm2_universe_auto",
                "flow_universe_auto",
            ):
                clean[key] = getattr(expanded, key)
        sym_keys = {
            "symbols",
            "sma_symbols",
            "blend_symbols",
            "flow_symbols",
            "mm_symbols",
            "mm2_symbols",
        }
        symbols_before: set[str] | None = None
        if sym_keys & clean.keys():
            symbols_before = set(engine._resolve_market_symbols())
        new_s = engine.apply_settings_patch(clean)
        if "strategy" in clean:
            try:
                strat_changed = engine.set_active_strategy(
                    normalize_strategy_name(str(clean["strategy"]))
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if strat_changed and engine.status is EngineStatus.RUNNING:
                await engine.refresh_market_universe()
        if symbols_before is not None:
            symbols_after = set(engine._resolve_market_symbols())
            if symbols_after != symbols_before:
                await engine.refresh_market_universe()
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("settings patch failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    logger.info("settings patched keys=%s", sorted(clean.keys()))
    raw = new_s.model_dump(mode="json")
    return {"ok": True, "settings": _mask_secrets(raw)}
