"""HTTP server: static page, links, and Hue room endpoints."""

import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from kiosk.config import ConfigError, Settings, load_settings
from kiosk.hue import HueClient, HueError

log = logging.getLogger("kiosk")

WEB_DIR = Path(__file__).resolve().parent / "web"

SETTINGS = web.AppKey("settings", Settings)
HUE = web.AppKey("hue", HueClient)
KNOWN_IDS = web.AppKey("known_ids", set)


OPEN_MODES = ("frame", "tab")
COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


class LinksError(Exception):
    pass


def read_links(path: Path) -> list[dict[str, str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("links file %s not found, showing no links", path)
        return []
    except OSError as exc:
        raise LinksError(f"cannot read links file: {exc.strerror}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LinksError("links file is not valid JSON") from exc
    if not isinstance(data, list):
        raise LinksError("links file must contain a JSON array")
    links: list[dict[str, str]] = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise LinksError(f"link {i} must be an object")
        title, url, icon = item.get("title"), item.get("url"), item.get("icon")
        open_mode, color = item.get("open"), item.get("color")
        if not isinstance(title, str) or not title.strip():
            raise LinksError(f"link {i} needs a title")
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
            raise LinksError(f"link {i} needs an http or https url")
        if icon is not None and not isinstance(icon, str):
            raise LinksError(f"link {i} icon must be a string")
        if open_mode is not None and open_mode not in OPEN_MODES:
            raise LinksError(f'link {i} open must be "frame" or "tab"')
        if color is not None and not (isinstance(color, str) and COLOR_RE.fullmatch(color)):
            raise LinksError(f"link {i} color must be a hex color like #a855f7")
        entry = {"title": title.strip(), "url": url.strip()}
        if icon:
            entry["icon"] = icon
        if open_mode is not None:
            entry["open"] = open_mode
        if color is not None:
            entry["color"] = color
        links.append(entry)
    return links


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_update(payload: object) -> tuple[bool | None, float | None]:
    """Validate a room update body. Raises ValueError with a short message."""
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    unknown = set(payload) - {"on", "brightness"}
    if unknown:
        raise ValueError(f"unknown field: {sorted(unknown)[0]}")
    on = payload.get("on")
    brightness = payload.get("brightness")
    if "on" in payload and not isinstance(on, bool):
        raise ValueError("on must be true or false")
    if "brightness" in payload:
        if not _is_number(brightness) or not 1 <= brightness <= 100:
            raise ValueError("brightness must be a number from 1 to 100")
        brightness = float(brightness)
    if on is None and brightness is None:
        raise ValueError("send on and/or brightness")
    if on is False and brightness is not None:
        raise ValueError("brightness cannot be set while turning off")
    return on, brightness


async def handle_links(request: web.Request) -> web.Response:
    try:
        return web.json_response(read_links(request.app[SETTINGS].links_file))
    except LinksError as exc:
        return _json_error(500, str(exc))


async def handle_rooms(request: web.Request) -> web.Response:
    try:
        overview = await request.app[HUE].overview()
    except HueError as exc:
        return _json_error(502, str(exc))
    known = request.app[KNOWN_IDS]
    known.clear()
    known.update(r["id"] for r in overview["rooms"])
    return web.json_response(overview)


async def handle_all_update(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return _json_error(400, "body must be JSON")
    if not isinstance(payload, dict) or set(payload) != {"on"} or not isinstance(payload["on"], bool):
        return _json_error(400, 'body must be {"on": true|false}')
    hue = request.app[HUE]
    try:
        group_id = await hue.all_group_id()
        if group_id is None:
            return _json_error(404, "no all-lights group on the bridge")
        await hue.set_group(group_id, {"on": {"on": payload["on"]}})
    except HueError as exc:
        return _json_error(502, str(exc))
    return web.json_response({"on": payload["on"]})


async def handle_room_update(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return _json_error(400, "body must be JSON")
    try:
        on, brightness = parse_update(payload)
    except ValueError as exc:
        return _json_error(400, str(exc))
    hue = request.app[HUE]
    known = request.app[KNOWN_IDS]
    room_id = request.match_info["id"]
    try:
        if room_id not in known:
            known.clear()
            known.update(r["id"] for r in await hue.rooms())
            if room_id not in known:
                return _json_error(404, "unknown room")
        body: dict[str, Any] = {}
        if on is not None:
            body["on"] = {"on": on}
        if brightness is not None:
            body["dimming"] = {"brightness": brightness}
            body["on"] = {"on": True}
        await hue.set_group(room_id, body)
    except HueError as exc:
        return _json_error(502, str(exc))
    return web.json_response({"id": room_id, "on": body["on"]["on"], "brightness": brightness})


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(settings: Settings) -> web.Application:
    app = web.Application()
    app[SETTINGS] = settings
    app[KNOWN_IDS] = set()

    async def hue_session(app: web.Application) -> Any:
        async with aiohttp.ClientSession() as session:
            app[HUE] = HueClient(settings.hue_base_url, settings.hue_app_key, session)
            yield

    app.cleanup_ctx.append(hue_session)
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/api/links", handle_links)
    app.router.add_get("/api/rooms", handle_rooms)
    app.router.add_post("/api/all", handle_all_update)
    app.router.add_post("/api/rooms/{id}", handle_room_update)
    app.router.add_static("/assets/", WEB_DIR, append_version=False)
    return app


def main(env: Mapping[str, str] | None = None) -> int:
    try:
        settings = load_settings(os.environ if env is None else env)
    except ConfigError as exc:
        print(f"kiosk-home: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log.info("starting on port %d, hue bridge %s", settings.port, settings.hue_host)
    web.run_app(create_app(settings), port=settings.port, print=None, access_log=None)
    return 0
