"""Small EarthMC API client used by the verification bot.

Only the endpoints required by this bot live here so Discord workflow code can
stay focused on member updates and command handling.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx


class EarthMCApiError(RuntimeError):
    """Raised when EarthMC cannot answer a request cleanly."""


class EarthMCApiClient:
    """Async wrapper for the EarthMC endpoints used by this project."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

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
        """Execute a POST request and normalize transport failures."""

        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EarthMCApiError(f"EarthMC API request failed for {path}") from exc
        return response.json()

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
