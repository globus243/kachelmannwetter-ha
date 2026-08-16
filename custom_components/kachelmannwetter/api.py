"""Asynchronous client for the Kachelmannwetter Public API v2."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

BASE_URL = "https://api.kachelmannwetter.com/v02"
REQUEST_TIMEOUT = 30


class KmwError(Exception):
    """Generic API error."""


class KmwAuthError(KmwError):
    """The API key is invalid."""


class KmwForbiddenError(KmwError):
    """Location not registered, or the daily limit is exhausted."""


class KmwApiClient:
    """Thin client that also keeps track of the rate-limit headers."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key
        self.rate_limit: int | None = None
        self.rate_remaining: int | None = None
        self.rate_retry_after: str | None = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = {"units": "metric"} | (params or {})
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.get(
                    f"{BASE_URL}{path}",
                    params=query,
                    headers={"X-API-Key": self._api_key},
                )
                raw = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KmwError(f"Connection error: {err}") from err

        if (limit := resp.headers.get("X-RateLimit-Limit")) and limit.isdigit():
            self.rate_limit = int(limit)
        if (rem := resp.headers.get("X-RateLimit-Remaining")) and rem.isdigit():
            self.rate_remaining = int(rem)
        if retry := resp.headers.get("x-ratelimit-retry-after"):
            self.rate_retry_after = retry

        if resp.status == 401:
            raise KmwAuthError("Invalid API key (HTTP 401)")
        if resp.status == 403:
            raise KmwForbiddenError(
                "HTTP 403: location is not registered as an API location, "
                "or the daily limit has been reached"
            )
        if resp.status == 429:
            raise KmwError(
                f"Rate limit reached, blocked until {self.rate_retry_after}"
            )
        if resp.status != 200:
            raise KmwError(f"HTTP {resp.status} for {path}")
        try:
            return json.loads(raw)
        except ValueError as err:
            raise KmwError(f"Invalid JSON response for {path}") from err

    async def current(self, lat: float, lon: float) -> dict[str, Any]:
        return await self._get(f"/current/{lat}/{lon}")

    async def forecast(
        self,
        lat: float,
        lon: float,
        package: str = "advanced",
        steps: str = "1h",
        model: str | None = None,
    ) -> dict[str, Any]:
        params = {"model": model} if model else None
        return await self._get(f"/forecast/{lat}/{lon}/{package}/{steps}", params)

    async def forecast_3day(
        self, lat: float, lon: float, model: str | None = None
    ) -> dict[str, Any]:
        params = {"model": model} if model else None
        return await self._get(f"/forecast/{lat}/{lon}/3day", params)

    async def trend14days(self, lat: float, lon: float) -> dict[str, Any]:
        return await self._get(f"/forecast/{lat}/{lon}/trend14days")

    async def astronomy(self, lat: float, lon: float) -> dict[str, Any]:
        return await self._get(f"/tools/astronomy/{lat}/{lon}")

    async def station_search(
        self, lat: float, lon: float, radius: int = 25
    ) -> list[dict[str, Any]]:
        return await self._get(f"/station/search/{lat}/{lon}", {"radius": radius})

    async def station_latest(self, station_id: str) -> dict[str, Any]:
        return await self._get(f"/station/{station_id}/observations/latest")
