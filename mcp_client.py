from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

import httpx

from tools import TOOL_NAME_TO_METHOD

logger = logging.getLogger(__name__)


class CircuitBreakerState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, threshold: int = 5, reset_seconds: float = 30.0) -> None:
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def record_success(self) -> None:
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED
            logger.info("Circuit breaker closed (recovered)")

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker opened (half-open test failed)")
        elif self.failure_count >= self.threshold and self.state == CircuitBreakerState.CLOSED:
            self.state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker opened after %d failures", self.threshold)

    def allow_request(self) -> bool:
        if self.state == CircuitBreakerState.CLOSED:
            return True
        if self.state == CircuitBreakerState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.reset_seconds:
                self.state = CircuitBreakerState.HALF_OPEN
                logger.info("Circuit breaker half-open (testing)")
                return True
            return False
        return True  # HALF_OPEN

    @property
    def is_open(self) -> bool:
        return self.state == CircuitBreakerState.OPEN


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, host: str, port: int, auth_token: str = "",
                 circuit_breaker_threshold: int = 5,
                 circuit_breaker_reset_seconds: float = 30.0) -> None:
        self.base_url = f"http://{host}:{port}"
        self.host = host
        self.port = port
        self._auth_token = auth_token
        self._headers = {}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._client = self._build_client()
        self._circuit_breaker = CircuitBreaker(
            threshold=circuit_breaker_threshold,
            reset_seconds=circuit_breaker_reset_seconds,
        )
        self._healthy = True
        self._last_ping_result = True
        self._health_check_interval = 5.0

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(180.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            http2=False,
        )

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def ping(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/ping", timeout=5.0)
            ok = r.status_code == 200
            self._last_ping_result = ok
            if ok:
                self._healthy = True
                self._circuit_breaker.record_success()
            return ok
        except httpx.ConnectError:
            self._healthy = False
            self._last_ping_result = False
            self._circuit_breaker.record_failure()
            return False
        except httpx.TimeoutException:
            self._healthy = False
            self._last_ping_result = False
            self._circuit_breaker.record_failure()
            return False
        except Exception:
            self._healthy = False
            self._last_ping_result = False
            return False

    async def health_stream(self, interval: float = 2.0) -> AsyncIterator[bool]:
        while True:
            yield await self.ping()
            await asyncio.sleep(interval)

    async def reconnect(self) -> bool:
        await self.close()
        self._client = self._build_client()
        ok = await self.ping()
        if ok:
            self._healthy = True
            self._circuit_breaker.state = CircuitBreakerState.CLOSED
            self._circuit_breaker.failure_count = 0
        return ok

    @property
    def is_healthy(self) -> bool:
        return self._healthy and not self._circuit_breaker.is_open

    def _get_timeout(self, endpoint: str, config_timeout: float = 180.0,
                     short_timeout: float = 10.0, long_timeout: float = 300.0) -> float:
        SHORT = {"/ping", "/env", "/cancel"}
        LONG = {"/speedtest", "/backup", "/restore", "/migrate", "/cloud-sync"}
        if endpoint in SHORT:
            return short_timeout
        if endpoint in LONG:
            return long_timeout
        return config_timeout

    async def call_endpoint(
        self, tool_name: str, endpoint: str, params: dict[str, Any] | None = None
    ) -> str | dict[str, Any]:
        if not self._circuit_breaker.allow_request():
            raise MCPError(f"Circuit breaker is open for {self.host}:{self.port}")

        if params is None:
            params = {}
        filtered = {k: v for k, v in params.items() if v is not None}
        method = TOOL_NAME_TO_METHOD.get(tool_name, "POST")
        url = f"{self.base_url}{endpoint}"
        timeout = self._get_timeout(endpoint)

        try:
            if method == "GET":
                r = await self._client.get(url, params=filtered if filtered else None, timeout=timeout)
            else:
                r = await self._client.post(url, json=filtered, timeout=timeout)

            r.raise_for_status()
            self._circuit_breaker.record_success()
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                return r.json()
            body = r.text
            return body.strip() if body.strip() else "(empty response)"
        except httpx.HTTPStatusError as e:
            body = e.response.text[:1000] if e.response.text else ""
            status = e.response.status_code
            # Don't count 4xx as circuit-breaker failures (client errors)
            if status >= 500:
                self._circuit_breaker.record_failure()
            raise MCPError(f"HTTP {status} on {endpoint}: {body}")
        except httpx.TimeoutException:
            self._circuit_breaker.record_failure()
            raise MCPError(f"Timeout ({timeout}s) on {endpoint}")
        except httpx.ConnectError:
            self._circuit_breaker.record_failure()
            raise MCPError(f"Cannot connect to {self.host}:{self.port}{endpoint}")
        except httpx.RequestError as e:
            self._circuit_breaker.record_failure()
            raise MCPError(f"Connection error on {endpoint}: {e}")

    async def call_with_retry(
        self, tool_name: str, endpoint: str, params: dict[str, Any] | None = None,
        max_retries: int = 1
    ) -> str | dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.call_endpoint(tool_name, endpoint, params)
            except MCPError as e:
                last_error = e
                err_str = str(e)
                if "Cannot connect" in err_str and attempt < max_retries:
                    logger.info("MCP disconnect (attempt %d/%d), reconnecting...",
                                attempt + 1, max_retries)
                    await asyncio.sleep(1 * (attempt + 1))
                    await self.reconnect()
                    continue
                if "Timeout" in err_str and attempt < max_retries:
                    logger.info("MCP timeout (attempt %d/%d), retrying...",
                                attempt + 1, max_retries)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
        raise MCPError(f"All {max_retries + 1} retries exhausted: {last_error}")
