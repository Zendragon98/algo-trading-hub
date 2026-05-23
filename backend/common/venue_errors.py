"""Shared helpers for venue rate-limit / IP-ban errors."""

from __future__ import annotations


def is_venue_throttle_error(exc: BaseException) -> bool:
    """True when the venue asked us to back off (not a trading reject)."""
    code = getattr(exc, "code", None)
    if code == -1003:
        return True
    status = getattr(exc, "status", None)
    if status in (418, 429):
        return True
    return getattr(exc, "retry_after_sec", None) is not None


def venue_throttle_sleep_sec(exc: BaseException, *, cap_sec: float = 86_400.0) -> float | None:
    """Seconds to sleep after a throttle error, or ``None`` if not throttled."""
    if not is_venue_throttle_error(exc):
        return None
    backoff = getattr(exc, "retry_after_sec", None)
    if backoff is not None:
        return min(float(backoff) + 1.0, cap_sec)
    return min(120.0, cap_sec)
