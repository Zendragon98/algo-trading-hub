"""Compatibility shim — prefer ``from common.config import Settings``."""

from common.config.aliases import normalize_strategy_name
from common.config.env import ENV_PATH
from common.config.settings import Settings, get_settings

__all__ = ["ENV_PATH", "Settings", "get_settings", "normalize_strategy_name"]
