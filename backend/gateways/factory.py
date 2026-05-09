"""Gateway selection.

The engine never imports a concrete venue. It asks `create_gateway(settings)`
for whatever `Settings.venue` says and gets back a `GatewayInterface`.

Adding a new venue is three steps:
    1. Implement a class that satisfies `GatewayInterface` under
       ``gateways/<venue>/<venue>_gateway.py``.
    2. Register it in `_REGISTRY` below.
    3. Set `VENUE=<venue>` in `.env` and provide whatever credentials
       that adapter expects.

The factory also performs a sanity-check that the configured trading
mode and venue-specific endpoints agree (e.g. you didn't ask for LIVE
but leave Binance pointed at the testnet host). Mismatches are
*warned* loudly rather than hard-rejected so a CI smoke test on
testnet can keep TRADING_MODE=paper while the engine still talks to
the real testnet host.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from common.config import Settings
from common.enums import TradingMode

from .gateway_interface import GatewayInterface

logger = logging.getLogger(__name__)


def _build_binance(settings: Settings) -> GatewayInterface:
    # Imported lazily so a missing optional dep on one venue can't crash
    # users of another.
    from .binance.binance_gateway import BinanceGateway

    if settings.is_live and settings.binance_testnet:
        logger.warning(
            "TRADING_MODE=live but BINANCE_TESTNET=true — engine will hit "
            "the testnet host with live mode safety. Set BINANCE_TESTNET=false "
            "and switch BINANCE_REST_BASE / BINANCE_WS_BASE to mainnet to actually trade live."
        )
    if not settings.is_live and not settings.binance_testnet:
        logger.warning(
            "TRADING_MODE=paper but BINANCE_TESTNET=false — engine will hit "
            "the live host with paper-mode synthetic-impact accounting. Recheck your config."
        )
    return BinanceGateway(settings)


def _build_ibkr(settings: Settings) -> GatewayInterface:
    from .ibkr.ibkr_gateway import IBKRGateway

    # Standard IB convention: paper account = port 7497, live = 7496.
    paper_port, live_port = 7497, 7496
    if settings.is_live and settings.ibkr_port == paper_port:
        logger.warning(
            "TRADING_MODE=live but IBKR_PORT=%d (the paper port). "
            "Switch to %d to actually trade live.",
            settings.ibkr_port,
            live_port,
        )
    if not settings.is_live and settings.ibkr_port == live_port:
        logger.warning(
            "TRADING_MODE=paper but IBKR_PORT=%d (the live port). Recheck your config.",
            settings.ibkr_port,
        )
    return IBKRGateway(settings)


# Keep the registry tiny + explicit. Add a new venue by adding a builder
# here and a `gateways/<venue>/` package implementing GatewayInterface.
_REGISTRY: dict[str, Callable[[Settings], GatewayInterface]] = {
    "binance": _build_binance,
    "ibkr": _build_ibkr,
}


def create_gateway(settings: Settings) -> GatewayInterface:
    """Return the gateway adapter selected by `settings.venue`."""
    builder = _REGISTRY.get(settings.venue)
    if builder is None:
        supported = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"unknown VENUE={settings.venue!r}; supported: {supported}"
        )
    mode_label = "LIVE" if settings.trading_mode is TradingMode.LIVE else "paper"
    logger.info("creating %s gateway in %s mode", settings.venue, mode_label)
    return builder(settings)


def supported_venues() -> list[str]:
    """List of venue ids the factory recognises."""
    return sorted(_REGISTRY)


__all__ = ["create_gateway", "supported_venues"]
