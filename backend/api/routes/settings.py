"""GET/PATCH /api/settings — read and update runtime ``Settings``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from engine.core.engine import Engine

from ..dependencies import get_engine

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
def patch_settings(
    patch: dict[str, Any] = Body(...),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Merge JSON fields into the engine's live ``Settings``.

    Omitted or placeholder secrets (``\"\"``, ``\"***\"``) keep existing keys.
    Values apply immediately where wired through ``Engine._apply_runtime_settings``;
    ``api_host`` / ``api_port`` need an API restart to change bind address.
    """
    try:
        new_s = engine.apply_settings_patch(patch)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw = new_s.model_dump(mode="json")
    return {"ok": True, "settings": _mask_secrets(raw)}
