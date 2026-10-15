"""Minimal Philips Hue CLIP v2 client: rooms and their grouped lights."""

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger("kiosk.hue")

_TIMEOUT = aiohttp.ClientTimeout(total=8)


class HueError(Exception):
    """The bridge could not be reached or rejected a request."""


class HueClient:
    def __init__(self, base_url: str, app_key: str, session: aiohttp.ClientSession) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"hue-application-key": app_key}
        self._session = session

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> list[Any]:
        url = f"{self._base}/clip/v2/resource/{path}"
        try:
            # The bridge presents a self-signed certificate; verification is off for it only.
            async with self._session.request(
                method, url, json=body, headers=self._headers, ssl=False, timeout=_TIMEOUT
            ) as resp:
                try:
                    payload = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("hue %s %s failed: %s", method, path, type(exc).__name__)
            raise HueError("Hue bridge unreachable") from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if status == 429:
            log.warning("hue %s %s -> 429", method, path)
            raise HueError("Hue bridge busy, try again")
        if not 200 <= status < 300 or errors:
            detail = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                detail = str(errors[0].get("description", ""))
            log.warning("hue %s %s -> %s %s", method, path, status, detail)
            raise HueError(f"Hue bridge error: {detail or f'HTTP {status}'}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise HueError("Hue bridge sent an unexpected response")
        return data

    async def rooms(self) -> list[dict[str, Any]]:
        """Return rooms as {id (grouped_light id), name, on, brightness}, sorted by name."""
        rooms, lights = await asyncio.gather(
            self._request("GET", "room"), self._request("GET", "grouped_light")
        )
        by_id = {item.get("id"): item for item in lights if isinstance(item, dict)}
        result: list[dict[str, Any]] = []
        for room in rooms:
            if not isinstance(room, dict):
                continue
            rid = next(
                (
                    s.get("rid")
                    for s in room.get("services", [])
                    if isinstance(s, dict) and s.get("rtype") == "grouped_light"
                ),
                None,
            )
            light = by_id.get(rid)
            if light is None:
                continue
            brightness = (light.get("dimming") or {}).get("brightness")
            result.append(
                {
                    "id": rid,
                    "name": (room.get("metadata") or {}).get("name", ""),
                    "on": bool((light.get("on") or {}).get("on", False)),
                    "brightness": brightness if isinstance(brightness, (int, float)) else None,
                }
            )
        result.sort(key=lambda r: (r["name"].casefold(), r["id"]))
        return result

    async def set_group(self, group_id: str, body: dict[str, Any]) -> None:
        await self._request("PUT", f"grouped_light/{group_id}", body)
