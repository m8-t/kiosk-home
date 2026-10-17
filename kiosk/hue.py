"""Minimal Philips Hue CLIP v2 client: rooms and their grouped lights."""

import asyncio
import logging
import ssl
from pathlib import Path
from time import monotonic
from typing import Any

import aiohttp

from kiosk.config import DEFAULT_HUE_CA_FILE, ConfigError

log = logging.getLogger("kiosk.hue")

_TIMEOUT = aiohttp.ClientTimeout(total=8)
_CERT_FAILED = "Hue bridge certificate check failed, check KIOSK_HUE_BRIDGE_ID"
OVERVIEW_TTL_S = 1.5


class HueError(Exception):
    """The bridge could not be reached or rejected a request."""


def _load_context(ca_file: Path) -> ssl.SSLContext:
    try:
        return ssl.create_default_context(cafile=str(ca_file))
    except OSError as exc:
        raise ConfigError(f"cannot load Hue CA file {ca_file}: {exc}") from exc


class HueClient:
    def __init__(
        self,
        base_url: str,
        app_key: str,
        session: aiohttp.ClientSession,
        bridge_id: str = "",
        ca_file: Path = DEFAULT_HUE_CA_FILE,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"hue-application-key": app_key}
        self._session = session
        # False leaves the certificate unchecked, the bridge id is what enables verification.
        self._ssl: ssl.SSLContext | bool = False
        self._server_hostname: str | None = None
        if bridge_id and self._base.startswith("https://"):
            self._ssl = _load_context(ca_file)
            self._server_hostname = bridge_id.lower()
        self._all_id: str | None = None
        self._all_known = False
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._inflight: asyncio.Task[dict[str, Any]] | None = None
        self._generation = 0

    async def all_group_id(self) -> str | None:
        """Grouped light id of the bridge_home resource (all lights), cached once found."""
        if self._all_known:
            return self._all_id
        data = await self._request("GET", "bridge_home")
        found = None
        for home in data:
            if not isinstance(home, dict):
                continue
            for svc in home.get("services", []):
                if isinstance(svc, dict) and svc.get("rtype") == "grouped_light":
                    found = svc.get("rid")
                    break
            if found:
                break
        self._all_id, self._all_known = found, True
        return found

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> list[Any]:
        url = f"{self._base}/clip/v2/resource/{path}"
        try:
            async with self._session.request(
                method,
                url,
                json=body,
                headers=self._headers,
                ssl=self._ssl,
                server_hostname=self._server_hostname,
                timeout=_TIMEOUT,
            ) as resp:
                try:
                    payload = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("hue %s %s failed: %s", method, path, type(exc).__name__)
            if self._server_hostname is not None and isinstance(exc, aiohttp.ClientSSLError):
                raise HueError(_CERT_FAILED) from exc
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

    async def _safe_all_id(self) -> str | None:
        try:
            return await self.all_group_id()
        except HueError:
            return None

    async def rooms(self) -> list[dict[str, Any]]:
        """Return rooms as {id (grouped_light id), name, on, brightness}, sorted by name.

        Always read from the bridge, never from the overview cache.
        """
        return (await self._read_overview())["rooms"]

    async def overview(self) -> dict[str, Any]:
        """Return {"all": {id, on} | None, "rooms": [...]}, cached for OVERVIEW_TTL_S.

        Concurrent callers share one bridge read. The result is shared, do not modify it.
        """
        if self._cached is not None and monotonic() - self._cached[0] < OVERVIEW_TTL_S:
            return self._cached[1]
        if self._inflight is None:
            self._inflight = asyncio.create_task(self._refresh_overview())
        return await asyncio.shield(self._inflight)

    async def _refresh_overview(self) -> dict[str, Any]:
        generation, started = self._generation, monotonic()
        try:
            overview = await self._read_overview()
        finally:
            if self._inflight is asyncio.current_task():
                self._inflight = None
        if generation == self._generation:
            self._cached = (started, overview)
        return overview

    def _invalidate_overview(self) -> None:
        self._generation += 1
        self._cached = None
        self._inflight = None

    async def _read_overview(self) -> dict[str, Any]:
        rooms, lights, all_id = await asyncio.gather(
            self._request("GET", "room"), self._request("GET", "grouped_light"), self._safe_all_id()
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
        all_light = by_id.get(all_id) if all_id else None
        all_state = (
            {"id": all_id, "on": bool((all_light.get("on") or {}).get("on", False))}
            if all_light is not None
            else None
        )
        return {"all": all_state, "rooms": result}

    async def set_group(self, group_id: str, body: dict[str, Any]) -> None:
        await self._request("PUT", f"grouped_light/{group_id}", body)
        self._invalidate_overview()
