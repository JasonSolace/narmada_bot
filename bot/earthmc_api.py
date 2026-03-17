"""Small EarthMC API client used by the verification bot.

Only the endpoints required by this bot live here so Discord workflow code can
stay focused on member updates and command handling.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
import time
from typing import Any, Optional

import httpx


class EarthMCApiError(RuntimeError):
    """Raised when EarthMC cannot answer a request cleanly."""


class EarthMCApiClient:
    """Async wrapper for the EarthMC endpoints used by this project."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        requests_per_minute: int = 30,
        max_rate_limit_retries: int = 3,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._request_lock = asyncio.Lock()
        self._min_interval_seconds = 60.0 / requests_per_minute
        self._max_rate_limit_retries = max(0, max_rate_limit_retries)
        self._next_request_at = 0.0

    async def close(self) -> None:
        """Release the shared HTTP client on shutdown."""

        await self._client.aclose()

    async def resolve_discord_link(self, discord_id: int) -> Optional[str]:
        """Resolve a Discord user ID to a linked Minecraft UUID, if one exists."""

        response = await self._post_json(
            "/discord",
            {
                "query": [
                    {
                        "type": "discord",
                        "target": str(discord_id),
                    }
                ]
            },
        )
        first_item = self._first_item(response)
        if not isinstance(first_item, dict):
            return None
        uuid = first_item.get("uuid")
        return str(uuid) if uuid else None

    async def fetch_player(self, uuid: str) -> Optional[dict[str, Any]]:
        """Fetch the small player payload needed to cache verification state."""

        response = await self._post_json(
            "/players",
            {
                "query": [uuid],
                "template": {
                    "name": True,
                    "uuid": True,
                    "timestamps": True,
                    "status": True,
                },
            },
        )
        first_item = self._first_item(response)
        if isinstance(first_item, dict):
            return first_item
        return None

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """Execute a throttled POST request and normalize transport failures."""

        for attempt in range(self._max_rate_limit_retries + 1):
            try:
                async with self._request_lock:
                    await self._sleep_until_ready()
                    response = await self._client.post(path, json=payload)
                    next_delay = self._min_interval_seconds
                    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                        next_delay = self._retry_delay_seconds(response, attempt)
                    self._next_request_at = time.monotonic() + next_delay
            except httpx.HTTPError as exc:
                raise EarthMCApiError(f"EarthMC API request failed for {path}") from exc

            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise EarthMCApiError(f"EarthMC API request failed for {path}") from exc
                return response.json()

            if attempt >= self._max_rate_limit_retries:
                raise EarthMCApiError(f"EarthMC API rate limit exceeded for {path}")

        raise EarthMCApiError(f"EarthMC API request failed for {path}")

    async def _sleep_until_ready(self) -> None:
        """Honor the currently scheduled earliest time for the next EarthMC request."""

        delay = self._next_request_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def _retry_delay_seconds(self, response: httpx.Response, attempt: int) -> float:
        """Prefer server-provided retry guidance; fall back to conservative exponential backoff."""

        retry_after = response.headers.get("Retry-After")
        if retry_after:
            parsed_retry_after = self._parse_retry_after(retry_after)
            if parsed_retry_after is not None:
                return parsed_retry_after
        return max(5.0, self._min_interval_seconds * (2 ** (attempt + 1)))

    @staticmethod
    def _parse_retry_after(value: str) -> Optional[float]:
        """Parse either delta-seconds or HTTP-date Retry-After header values."""

        try:
            return max(0.0, float(value))
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None

        now = datetime.now(retry_at.tzinfo)
        return max(0.0, (retry_at - now).total_seconds())

    @staticmethod
    def _first_item(payload: Any) -> Optional[Any]:
        """EarthMC responses are usually lists; this keeps parsing code simple."""

        if isinstance(payload, list):
            return payload[0] if payload else None
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return results[0] if results else None
        return None
