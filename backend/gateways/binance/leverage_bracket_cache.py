"""On-disk cache for Binance USDT-M leverage bracket caps.

Persists max selectable leverage per symbol so ``connect()`` avoids calling
``GET /fapi/v1/leverageBracket`` on every engine start after the first
successful fetch. Invalidated when ``BINANCE_REST_BASE`` changes.

Relative paths are resolved against ``backend/`` (same rule as ``PERSIST_DIR``).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from common.config import Settings

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1


def _normalize_rest_base(url: str) -> str:
    return str(url).strip().rstrip("/")


def backend_root() -> Path:
    """Return ``backend/`` (parent of ``gateways``)."""
    return Path(__file__).resolve().parent.parent.parent


def leverage_bracket_cache_path(settings: Settings) -> Path:
    p = Path(settings.leverage_bracket_cache_path)
    if not p.is_absolute():
        p = backend_root() / p
    return p


def load_leverage_bracket_cache(
    path: Path,
    rest_base: str,
    ttl_sec: int,
    *,
    ignore_ttl: bool = False,
) -> dict[str, int] | None:
    """Return caps map if the cache file is usable; otherwise ``None``."""
    want = _normalize_rest_base(rest_base)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("leverage bracket cache unreadable (%s): %s", path, exc)
        return None

    if int(data.get("version", 0)) != _CACHE_VERSION:
        return None
    if _normalize_rest_base(str(data.get("rest_base", ""))) != want:
        logger.info(
            "leverage bracket cache ignored (REST host changed): %s",
            path,
        )
        return None

    fetched = data.get("fetched_at")
    try:
        fetched_at = float(fetched)
    except (TypeError, ValueError):
        return None

    now = time.time()
    if not ignore_ttl and ttl_sec > 0 and (now - fetched_at) > ttl_sec:
        return None

    caps_raw = data.get("caps")
    if not isinstance(caps_raw, dict) or not caps_raw:
        return None

    caps: dict[str, int] = {}
    for sym, val in caps_raw.items():
        su = str(sym).upper()
        try:
            caps[su] = int(val)
        except (TypeError, ValueError):
            continue
    return caps if caps else None


def save_leverage_bracket_cache(path: Path, rest_base: str, caps: dict[str, int]) -> None:
    """Atomically write caps to disk."""
    if not caps:
        return
    payload = {
        "version": _CACHE_VERSION,
        "rest_base": _normalize_rest_base(rest_base),
        "fetched_at": time.time(),
        "caps": {k.upper(): int(v) for k, v in caps.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    logger.debug("wrote leverage bracket cache (%d symbols) -> %s", len(caps), path)
