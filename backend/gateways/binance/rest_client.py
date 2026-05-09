"""Thin signed-REST wrapper for Binance USDT-M Futures.

Uses `httpx.AsyncClient` directly (rather than the official binance-connector
`UMFutures` synchronous client) so all REST calls live on the same event
loop as the WebSocket consumers and the engine. Keeps the call sites
explicit and easy to test.

Only the endpoints we actually use are exposed; we do not aim to cover
the full Binance surface.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# The Futures Testnet uses /fapi/v1 for everything we need.
_FAPI = "/fapi/v1"
_FAPI_V2 = "/fapi/v2"


class BinanceRestError(RuntimeError):
    """Raised when Binance returns a non-2xx response or a `code < 0` body."""

    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"binance rest error status={status} code={code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class BinanceRestClient:
    """Async HTTP client that signs private requests with HMAC-SHA256."""

    def __init__(self, base_url: str, api_key: str, api_secret: str) -> None:
        # 10s is generous for testnet which can lag spot in latency.
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._api_key = api_key
        self._api_secret = api_secret.encode()

    async def close(self) -> None:
        await self._client.aclose()

    # --- Public endpoints ---

    async def server_time(self) -> int:
        """Return Binance server time in ms. Used to detect clock skew."""
        data = await self._get(f"{_FAPI}/time", signed=False)
        return int(data["serverTime"])

    async def exchange_info(self) -> dict[str, Any]:
        return await self._get(f"{_FAPI}/exchangeInfo", signed=False)

    async def klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return await self._get(f"{_FAPI}/klines", params=params, signed=False)

    async def book_snapshot(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        return await self._get(
            f"{_FAPI}/depth",
            params={"symbol": symbol, "limit": limit},
            signed=False,
        )

    async def agg_trades(
        self,
        symbol: str,
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return await self._get(f"{_FAPI}/aggTrades", params=params, signed=False)

    # --- Private endpoints ---

    async def account(self) -> dict[str, Any]:
        return await self._get(f"{_FAPI_V2}/account", signed=True)

    async def position_risk(self) -> list[dict[str, Any]]:
        return await self._get(f"{_FAPI_V2}/positionRisk", signed=True)

    async def new_order(self, **params: Any) -> dict[str, Any]:
        return await self._post(f"{_FAPI}/order", params=params, signed=True)

    async def cancel_order(self, **params: Any) -> dict[str, Any]:
        return await self._delete(f"{_FAPI}/order", params=params, signed=True)

    async def listen_key(self) -> str:
        """Create a user-data stream listenKey. Valid 60 minutes."""
        data = await self._post(f"{_FAPI}/listenKey", params={}, signed=False, key_only=True)
        return data["listenKey"]

    async def keepalive_listen_key(self) -> None:
        await self._put(f"{_FAPI}/listenKey", params={}, signed=False, key_only=True)

    # --- Internal helpers ---

    async def _get(self, path: str, *, params: dict[str, Any] | None = None, signed: bool) -> Any:
        return await self._request("GET", path, params=params or {}, signed=signed, key_only=False)

    async def _post(
        self,
        path: str,
        *,
        params: dict[str, Any],
        signed: bool,
        key_only: bool = False,
    ) -> Any:
        return await self._request("POST", path, params=params, signed=signed, key_only=key_only)

    async def _put(
        self,
        path: str,
        *,
        params: dict[str, Any],
        signed: bool,
        key_only: bool = False,
    ) -> Any:
        return await self._request("PUT", path, params=params, signed=signed, key_only=key_only)

    async def _delete(self, path: str, *, params: dict[str, Any], signed: bool) -> Any:
        return await self._request("DELETE", path, params=params, signed=signed, key_only=False)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any],
        signed: bool,
        key_only: bool,
    ) -> Any:
        # Sort to keep HMAC reproducible across Python versions and dicts.
        cleaned = {k: v for k, v in params.items() if v is not None}
        headers: dict[str, str] = {}

        if signed:
            cleaned.setdefault("recvWindow", 5000)
            cleaned["timestamp"] = int(time.time() * 1000)
            query = urlencode(cleaned, doseq=True)
            signature = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
            cleaned["signature"] = signature

        if signed or key_only:
            headers["X-MBX-APIKEY"] = self._api_key

        try:
            response = await self._client.request(method, path, params=cleaned, headers=headers)
        except httpx.HTTPError as exc:  # network-level failure
            raise BinanceRestError(0, None, f"transport: {exc}") from exc

        if response.status_code >= 400:
            self._raise_from(response)

        return response.json()

    def _raise_from(self, response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            raise BinanceRestError(response.status_code, None, response.text) from None
        # Binance returns {"code": -2010, "msg": "..."} on errors.
        raise BinanceRestError(response.status_code, body.get("code"), body.get("msg", str(body)))
