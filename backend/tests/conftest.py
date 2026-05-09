"""Shared pytest fixtures.

Adds the backend/ directory to sys.path so tests can import top-level
packages (`common`, `engine`, `gateways`, ...) the same way the runtime
does, without requiring an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
