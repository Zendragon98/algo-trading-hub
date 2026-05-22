"""Shared pytest fixtures.

Adds the backend/ directory to sys.path so tests can import top-level
packages (`common`, `engine`, `gateways`, ...) the same way the runtime
does, without requiring an editable install.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Captured at conftest import (before any test patches the stdlib clock).
_REAL_TIME = time.time


@pytest.fixture(autouse=True)
def _restore_stdlib_time() -> None:
    """Ensure backtests or strategies cannot leave a patched ``time.time``."""
    yield
    time.time = _REAL_TIME
