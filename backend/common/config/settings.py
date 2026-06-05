from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from common.breaker_registry import merge_breaker_enabled
from common.enums import TradingMode
from .env import ENV_PATH
from .sections.api_persist import ApiPersistMixin
from .sections.blend import BlendMixin
from .sections.engine_boot import EngineBootMixin
from .sections.execution_core import ExecutionCoreMixin
from .sections.flow import FlowMixin
from .sections.mm2 import Mm2Mixin
from .sections.mm_institutional import MmInstitutionalMixin
from .sections.mm_legacy import MmLegacyMixin
from .sections.multi_strategy import MultiStrategyMixin
from .sections.pairs import PairsMixin
from .sections.risk import RiskMixin
from .sections.sma import SmaMixin
from .sections.venue import VenueMixin


class Settings(
    BaseSettings,
    VenueMixin,
    EngineBootMixin,
    RiskMixin,
    PairsMixin,
    ExecutionCoreMixin,
    SmaMixin,
    BlendMixin,
    FlowMixin,
    MultiStrategyMixin,
    MmLegacyMixin,
    Mm2Mixin,
    MmInstitutionalMixin,
    ApiPersistMixin,
):
    """Strongly-typed view of the environment."""

    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("mm_symbol_half_spread_bps", mode="before")
    @classmethod
    def _parse_mm_symbol_half_spread(cls, value: object) -> object:
        return cls._parse_symbol_float_map(value)

    @field_validator("mm_symbol_quote_overrides", mode="before")
    @classmethod
    def _parse_mm_symbol_overrides(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            value = json.loads(text)
        if not isinstance(value, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for sym_key, fields in value.items():
            sym = str(sym_key).strip().upper()
            if not sym or not isinstance(fields, dict):
                continue
            out[sym] = {str(k): float(v) for k, v in fields.items()}
        return out

    @staticmethod
    def _parse_symbol_float_map(value: object) -> dict[str, float]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {str(k).strip().upper(): float(v) for k, v in value.items() if str(k).strip()}
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            if text.startswith("{"):
                raw: Any = json.loads(text)
                return {str(k).strip().upper(): float(v) for k, v in raw.items() if str(k).strip()}
            out: dict[str, float] = {}
            for part in text.split(","):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                sym, val = part.split(":", 1)
                out[sym.strip().upper()] = float(val.strip())
            return out
        return {}

    @field_validator(
        "symbols",
        "sma_symbols",
        "blend_symbols",
        "flow_symbols",
        "mm_symbols",
        "mm2_symbols",
        "mm_auto_pin_symbols",
        "mm_universe_regime_symbols",
        "multi_strategy_pair_bases",
        "cors_origins",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # pydantic-settings hands us a raw string from the .env file; split
        # it into a list so callers always see `list[str]`.
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("venue", mode="before")
    @classmethod
    def _normalise_venue(cls, value: object) -> object:
        # Case-insensitive venue ids so VENUE=Binance and VENUE=binance both work.
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def is_live(self) -> bool:
        """Convenience: True when running against real money."""
        return self.trading_mode is TradingMode.LIVE

    def is_breaker_enabled(self, code: str) -> bool:
        """Whether ``code`` may trip the shared circuit breaker."""
        if code == "operator_halt":
            return True
        return bool(self.breaker_enabled.get(code, True))

    @model_validator(mode="after")
    def _sync_md_crossed_book_breaker(self) -> Settings:
        """Sync legacy md_crossed_book_breaker with breaker_enabled."""
        merged = merge_breaker_enabled(
            {"md_crossed_book": self.md_crossed_book_breaker},
            base=self.breaker_enabled,
        )
        self.breaker_enabled = merged
        return self

    @property
    def env_path(self) -> Path:
        """Path the settings were loaded from. Helpful in error messages."""
        return ENV_PATH

    def pair_legs(self) -> list[tuple[str, str]]:
        """Return (USDT-leg, USDC-leg) tuples derived from `symbols`.

        The pairs-trading strategy expects matched pairs like
        ``("BTCUSDT", "BTCUSDC")``. Any symbol without a partner is dropped
        and reported by the strategy at startup, not here.
        """
        usdt = {s.removesuffix("USDT"): s for s in self.symbols if s.endswith("USDT")}
        usdc = {s.removesuffix("USDC"): s for s in self.symbols if s.endswith("USDC")}
        bases = sorted(usdt.keys() & usdc.keys())
        return [(usdt[base], usdc[base]) for base in bases]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton settings instance."""
    return Settings()

