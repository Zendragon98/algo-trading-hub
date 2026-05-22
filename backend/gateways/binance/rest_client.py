"""Thin signed-REST wrapper for Binance USDT-M Futures.

Uses `httpx.AsyncClient` directly (rather than the official binance-connector
`UMFutures` synchronous client) so all REST calls live on the same event
loop as the WebSocket consumers and the engine. Keeps the call sites
explicit and easy to test.

Client-side spacing plus HTTP 429 / ban handling: all requests go through
one asyncio lock with a minimum interval; HTTP 429 reads ``Retry-After``
and blocks further requests until that window elapses (Binance guidance).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# The Futures Testnet uses /fapi/v1 for everything we need.
_FAPI = "/fapi/v1"
_FAPI_V2 = "/fapi/v2"

_BAN_UNTIL_MS = re.compile(r"banned until (\d+)", re.IGNORECASE)
# When Binance returns -1003 without an explicit unban timestamp (rare).
_RATE_LIMIT_DEFAULT_BACKOFF_SEC = 120.0
_RATE_LIMIT_MAX_BACKOFF_SEC = 86_400.0


def _retry_after_from_rate_limit_message(message: str) -> float | None:
    """Parse seconds until IP ban / throttle lifts from Binance -1003 text."""
    m = _BAN_UNTIL_MS.search(message)
    if not m:
        return None
    until_ms = int(m.group(1))
    now_ms = int(time.time() * 1000)
    return max(0.0, (until_ms - now_ms) / 1000.0)


def parse_retry_after_header(headers: httpx.Headers) -> float | None:
    """Parse ``Retry-After`` as seconds (RFC 7231) or HTTP-date.

    Returns seconds to wait from *now*, or ``None`` if absent / unparsable.
    """
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    text = str(raw).strip()
    if text.isdigit():
        return float(text)
    try:
        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, OSError):
        return None


class BinanceRestError(RuntimeError):
    """Raised when Binance returns a non-2xx response or a `code < 0` body."""

    def __init__(
        self,
        status: int,
        code: int | None,
        message: str,
        *,
        retry_after_sec: float | None = None,
    ) -> None:
        super().__init__(f"binance rest error status={status} code={code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        if retry_after_sec is not None:
            self.retry_after_sec = min(float(retry_after_sec), _RATE_LIMIT_MAX_BACKOFF_SEC)
        else:
            self.retry_after_sec = None
            ban_delay = _retry_after_from_rate_limit_message(message)
            if code == -1003:
                self.retry_after_sec = (
                    min(ban_delay, _RATE_LIMIT_MAX_BACKOFF_SEC)
                    if ban_delay is not None
                    else min(_RATE_LIMIT_DEFAULT_BACKOFF_SEC, _RATE_LIMIT_MAX_BACKOFF_SEC)
                )
            elif status == 418 and ban_delay is not None:
                self.retry_after_sec = min(ban_delay, _RATE_LIMIT_MAX_BACKOFF_SEC)


class BinanceRestClient:
    """Async HTTP client that signs private requests with HMAC-SHA256."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        min_interval_sec: float = 0.05,
        rest_429_default_backoff_sec: float = 60.0,
        rest_pause_buffer_sec: float = 0.5,
    ) -> None:
        # 10s is generous for testnet which can lag spot in latency.
        self._base_url = base_url
        self._timeout = 10.0
        self._client = httpx.AsyncClient(base_url=base_url, timeout=self._timeout)
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._min_interval_sec = max(0.0, float(min_interval_sec))
        self._429_default_sec = max(1.0, float(rest_429_default_backoff_sec))
        self._pause_buffer_sec = max(0.0, float(rest_pause_buffer_sec))
        # Serializes REST calls + coordinates spacing vs global pause window.
        self._gate = asyncio.Lock()
        self._pause_until: float = 0.0
        self._last_send_end_at: float = 0.0
        # server_ms - local_ms; applied to signed request timestamps (fixes Binance -1021 skew).
        self._time_offset_ms: int = 0

    def _extend_global_pause(self, seconds: float, *, reason: str) -> float:
        """Block all subsequent requests until now + seconds (+ buffer).

        Returns the enforced delay (seconds), including buffer, capped at one day.
        """
        delay = min(float(seconds) + self._pause_buffer_sec, _RATE_LIMIT_MAX_BACKOFF_SEC)
        until = time.time() + delay
        if until > self._pause_until:
            self._pause_until = until
            logger.warning(
                "binance REST: suspending requests ~%.1fs (%s)",
                until - time.time(),
                reason,
            )
        return delay

    async def close(self) -> None:
        await self._client.aclose()

    def _ensure_open(self) -> None:
        # The engine can be stop/started via API control endpoints. On stop we
        # close the underlying AsyncClient, so on restart we need to recreate it.
        if self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)

    def _sign_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Attach timestamp + HMAC signature. Must run immediately before HTTP send."""
        send = dict(params)
        send.setdefault("recvWindow", 5000)
        send["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        query = urlencode(send, doseq=True)
        send["signature"] = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        return send

    async def _execute_rate_limited_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any],
        signed: bool,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Single-flight HTTP call: pause window, min spacing, then transport.

        Signed requests are HMAC'd here (after any throttle sleep) so the
        timestamp stays inside Binance ``recvWindow`` even when the queue waits
        seconds between signing and send (otherwise ``code=-1021``).
        """
        async with self._gate:
            now = time.time()
            if now < self._pause_until:
                wait = self._pause_until - now
                if wait > 0:
                    logger.warning(
                        "binance REST: waiting %.1fs (global rate-limit pause)",
                        wait,
                    )
                    await asyncio.sleep(wait)
            if self._min_interval_sec > 0.0 and self._last_send_end_at > 0.0:
                gap = self._last_send_end_at + self._min_interval_sec - time.time()
                if gap > 0:
                    await asyncio.sleep(gap)
            send_params = self._sign_params(params) if signed else params
            self._ensure_open()
            try:
                return await self._client.request(method, path, params=send_params, headers=headers)
            finally:
                self._last_send_end_at = time.time()

    # --- Public endpoints ---

    async def server_time(self) -> int:
        """Return Binance server time in ms. Used to detect clock skew."""
        data = await self._get(f"{_FAPI}/time", signed=False)
        return int(data["serverTime"])

    async def sync_server_time(self) -> int:
        """Align signed-request timestamps with Binance's clock.

        Without this, a Windows/host clock even slightly ahead of the venue
        yields ``code=-1021`` (timestamp ahead of server) on private endpoints.
        """
        server_ms = await self.server_time()
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = server_ms - local_ms
        return server_ms

    @property
    def time_offset_ms(self) -> int:
        """Delta applied to local clock for signed requests (ms)."""
        return self._time_offset_ms

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

    async def book_ticker(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Best bid/ask for one symbol or the full venue (unsigned)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.upper()
        result = await self._get(f"{_FAPI}/ticker/bookTicker", params=params, signed=False)
        if isinstance(result, dict):
            return [result]
        return result

    async def ticker_24hr(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return 24h rolling window stats for one or every symbol.

        With no ``symbol`` the venue returns one row per listed contract;
        used to size strategy weights from per-symbol notional volume.
        """
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.upper()
        result = await self._get(f"{_FAPI}/ticker/24hr", params=params, signed=False)
        # Single-symbol queries return a dict; normalise to a list so callers
        # don't need to branch.
        if isinstance(result, dict):
            return [result]
        return result

    async def fetch_24h_volumes(self, symbols: list[str]) -> dict[str, float]:
        """Return 24h quote-asset notional volume for each requested symbol."""
        return {
            sym: vol for sym, (vol, _) in (await self.fetch_24h_stats(symbols)).items()
        }

    async def fetch_24h_stats(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, float]]:
        """Return ``(quote_volume, last_price)`` per requested symbol."""
        wanted = {s.upper() for s in symbols}
        rows = await self.ticker_24hr()
        out: dict[str, tuple[float, float]] = {}
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            if sym not in wanted:
                continue
            qv = row.get("quoteVolume")
            lp = row.get("lastPrice")
            if qv is None or lp is None:
                continue
            try:
                out[sym] = (float(qv), float(lp))
            except (TypeError, ValueError):
                continue
        return out

    # --- Private endpoints ---

    async def account(self) -> dict[str, Any]:
        return await self._get(f"{_FAPI_V2}/account", signed=True)

    async def position_risk(self) -> list[dict[str, Any]]:
        return await self._get(f"{_FAPI_V2}/positionRisk", signed=True)

    async def new_order(self, **params: Any) -> dict[str, Any]:
        return await self._post(f"{_FAPI}/order", params=params, signed=True)

    async def cancel_order(self, **params: Any) -> dict[str, Any]:
        return await self._delete(f"{_FAPI}/order", params=params, signed=True)

    async def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.upper()
        data = await self._get(f"{_FAPI}/openOrders", params=params, signed=True)
        if isinstance(data, list):
            return data
        return []

    async def query_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any] | None:
        """Return a single order row by ``origClientOrderId``, or ``None`` if unknown (-2013)."""
        try:
            return await self._get(
                f"{_FAPI}/order",
                params={
                    "symbol": symbol.upper(),
                    "origClientOrderId": orig_client_order_id,
                },
                signed=True,
            )
        except BinanceRestError as exc:
            if exc.code == -2013:
                return None
            raise

    async def leverage_brackets(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return cross-margin leverage brackets for one or all symbols.

        When ``symbol`` is omitted, the venue returns every listed contract
        in one response (used to cap engine ``LEVERAGE`` per symbol).
        """
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.upper()
        data = await self._get(f"{_FAPI}/leverageBracket", params=params, signed=True)
        if isinstance(data, list):
            return data
        return [data]

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Change initial leverage for `symbol` (allowed range is symbol-specific)."""
        return await self._post(
            f"{_FAPI}/leverage",
            params={"symbol": symbol.upper(), "leverage": int(leverage)},
            signed=True,
        )

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
        _skew_retry: bool = False,
    ) -> Any:
        cleaned = {k: v for k, v in params.items() if v is not None}
        headers: dict[str, str] = {}

        if signed or key_only:
            headers["X-MBX-APIKEY"] = self._api_key

        try:
            response = await self._execute_rate_limited_request(
                method, path, params=cleaned, signed=signed, headers=headers,
            )
        except httpx.HTTPError as exc:  # network-level failure
            raise BinanceRestError(0, None, f"transport: {exc}") from exc

        # HTTP 429: honour Retry-After and pause all further REST calls.
        if response.status_code == 429:
            header_delay = parse_retry_after_header(response.headers)
            effective = header_delay if header_delay is not None else self._429_default_sec
            enforced = self._extend_global_pause(
                effective,
                reason=(
                    f"HTTP 429 Retry-After={header_delay}"
                    if header_delay is not None
                    else f"HTTP 429 (no Retry-After, default {self._429_default_sec}s)"
                ),
            )
            msg = response.text[:500] if response.text else "Too Many Requests"
            raise BinanceRestError(
                429,
                None,
                msg,
                retry_after_sec=enforced,
            )

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                err = BinanceRestError(response.status_code, None, response.text)
                if err.retry_after_sec:
                    self._extend_global_pause(
                        err.retry_after_sec,
                        reason=f"HTTP {response.status_code} non-JSON body",
                    )
                raise err from None
            code = body.get("code")
            # Clock drift vs Binance (-1021): re-sync once and retry signed calls.
            if signed and code == -1021 and not _skew_retry:
                await self.sync_server_time()
                return await self._request(
                    method,
                    path,
                    params=params,
                    signed=signed,
                    key_only=key_only,
                    _skew_retry=True,
                )
            err = BinanceRestError(
                response.status_code,
                code,
                body.get("msg", str(body)),
            )
            if err.retry_after_sec:
                self._extend_global_pause(
                    err.retry_after_sec,
                    reason=f"code={code} msg slice",
                )
            raise err

        return response.json()
