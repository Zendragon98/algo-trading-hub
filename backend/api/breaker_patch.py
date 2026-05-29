"""Validate and build breaker_enabled settings patches."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from common.breaker_registry import (
    BREAKER_REGISTRY,
    LIVE_DISABLE_CONFIRM_TOKEN,
    majors_being_disabled,
    merge_breaker_enabled,
)
from common.config import Settings


def breaker_registry_dtos() -> list[dict[str, Any]]:
    return [
        {
            "code": d.code,
            "severity": d.severity,
            "scope": d.scope,
            "label": d.label,
            "description": d.description,
            "group": d.group,
            "default_enabled": d.default_enabled,
            "disableable": d.disableable,
        }
        for d in BREAKER_REGISTRY
    ]


def assert_live_major_disable_allowed(
    settings: Settings,
    patch: dict[str, bool],
    *,
    confirm_live_disable: bool,
    confirm_token: str,
) -> None:
    """Reject disabling major breakers in LIVE without explicit confirmation."""
    if not settings.is_live:
        return
    disabled = majors_being_disabled(settings.breaker_enabled, patch)
    if not disabled:
        return
    if not confirm_live_disable:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "live_major_breaker_disable_requires_confirmation",
                "codes": disabled,
                "message": (
                    "Disabling major circuit breakers in LIVE requires "
                    "confirm_live_disable=true and confirm_token."
                ),
            },
        )
    if confirm_token.strip() != LIVE_DISABLE_CONFIRM_TOKEN:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_confirm_token",
                "expected": LIVE_DISABLE_CONFIRM_TOKEN,
                "codes": disabled,
            },
        )


def build_breaker_enabled_settings_patch(
    settings: Settings,
    body: dict[str, bool],
    *,
    confirm_live_disable: bool = False,
    confirm_token: str = "",
) -> dict[str, Any]:
    """Return a ``apply_settings_patch`` fragment for ``breaker_enabled``."""
    merged = merge_breaker_enabled(body, base=settings.breaker_enabled)
    assert_live_major_disable_allowed(
        settings,
        body,
        confirm_live_disable=confirm_live_disable,
        confirm_token=confirm_token,
    )
    return {"breaker_enabled": merged}
