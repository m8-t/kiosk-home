import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiosk.app import create_app
from kiosk.config import ConfigError, Settings, load_settings

KEY = "secret-app-key"


class FakeHue:
    def __init__(self) -> None:
        self.rooms: list[dict[str, Any]] = [
            {
                "id": "room-k",
                "metadata": {"name": "Kitchen"},
                "services": [
                    {"rtype": "device", "rid": "dev-1"},
                    {"rtype": "grouped_light", "rid": "gl-k"},
                ],
            },
            {
                "id": "room-b",
                "metadata": {"name": "bedroom"},
                "services": [{"rtype": "grouped_light", "rid": "gl-b"}],
            },
            {"id": "room-x", "metadata": {"name": "Orphan"}, "services": []},
        ]
        self.lights: dict[str, dict[str, Any]] = {
            "gl-k": {"id": "gl-k", "on": {"on": True}, "dimming": {"brightness": 54.3}},
            "gl-b": {"id": "gl-b", "on": {"on": False}},
        }
        self.puts: list[tuple[str, dict[str, Any]]] = []
        self.keys: list[str | None] = []
        self.put_status = 200
        self.put_errors: list[dict[str, str]] = []
        self.get_status = 200
        self.app = web.Application()
        self.app.router.add_get("/clip/v2/resource/room", self.get_rooms)
        self.app.router.add_get("/clip/v2/resource/grouped_light", self.get_lights)
        self.app.router.add_put("/clip/v2/resource/grouped_light/{id}", self.put_light)

    async def get_rooms(self, request: web.Request) -> web.Response:
        self.keys.append(request.headers.get("hue-application-key"))
        return web.json_response({"errors": [], "data": self.rooms}, status=self.get_status)

    async def get_lights(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"errors": [], "data": list(self.lights.values())}, status=self.get_status
        )

    async def put_light(self, request: web.Request) -> web.Response:
        self.keys.append(request.headers.get("hue-application-key"))
        self.puts.append((request.match_info["id"], await request.json()))
        return web.json_response({"errors": self.put_errors, "data": []}, status=self.put_status)


class Env:
    def __init__(self, client: TestClient, hue: FakeHue, links: Path) -> None:
        self.client, self.hue, self.links = client, hue, links


@asynccontextmanager
async def rig(tmp_path: Path) -> AsyncIterator[Env]:
    hue = FakeHue()
    links = tmp_path / "links.json"
    links.write_text(json.dumps([{"title": "Nav", "url": "https://music.example.org"}]))
    async with TestServer(hue.app) as hue_server:
        settings = Settings(
            hue_host=f"http://127.0.0.1:{hue_server.port}", hue_app_key=KEY, links_file=links
        )
        async with TestClient(TestServer(create_app(settings))) as client:
            yield Env(client, hue, links)


async def test_rooms_join_and_sort(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.get("/api/rooms")
        assert resp.status == 200
        assert await resp.json() == [
            {"id": "gl-b", "name": "bedroom", "on": False, "brightness": None},
            {"id": "gl-k", "name": "Kitchen", "on": True, "brightness": 54.3},
        ]
        assert set(env.hue.keys) == {KEY}


async def test_healthz_does_not_contact_hue(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.get("/healthz")
        assert resp.status == 200
        assert env.hue.keys == []


async def test_index_served(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.get("/")
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        assert (await env.client.get("/assets/logic.js")).status == 200


async def test_toggle(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 200
        assert await resp.json() == {"id": "gl-k", "on": False, "brightness": None}
        assert env.hue.puts == [("gl-k", {"on": {"on": False}})]


async def test_dim_always_sends_on_true(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-k", json={"brightness": 42})
        assert resp.status == 200
        assert env.hue.puts == [("gl-k", {"dimming": {"brightness": 42.0}, "on": {"on": True}})]
        assert await resp.json() == {"id": "gl-k", "on": True, "brightness": 42.0}


async def test_post_with_cached_id_makes_only_the_put(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        env.hue.keys.clear()
        resp = await env.client.post("/api/rooms/gl-k", json={"brightness": 30})
        assert resp.status == 200
        assert len(env.hue.keys) == 1
        assert len(env.hue.puts) == 1


async def test_unknown_id_refreshes_once_then_404(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/nope", json={"on": True})
        assert resp.status == 404
        assert len(env.hue.keys) == 1
        assert env.hue.puts == []


async def test_unknown_id_found_after_refresh(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-b", json={"on": True})
        assert resp.status == 200
        assert len(env.hue.puts) == 1


async def test_hue_429_busy_message(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.put_status = 429
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 502
        assert (await resp.json())["error"] == "Hue bridge busy, try again"


async def test_dim_off_room_adds_on(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-b", json={"brightness": 10})
        assert resp.status == 200
        assert env.hue.puts == [("gl-b", {"dimming": {"brightness": 10.0}, "on": {"on": True}})]
        assert await resp.json() == {"id": "gl-b", "on": True, "brightness": 10.0}


async def test_unknown_room_404(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/nope", json={"on": True})
        assert resp.status == 404
        assert env.hue.puts == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"on": "yes"},
        {"on": 1},
        {"brightness": 0},
        {"brightness": 101},
        {"brightness": "50"},
        {"brightness": True},
        {"on": False, "brightness": 20},
        {"color": "red", "on": True},
        [1],
    ],
)
async def test_validation_errors(tmp_path: Path, payload: Any) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-k", json=payload)
        assert resp.status == 400
        assert "error" in await resp.json()
        assert env.hue.puts == []


async def test_invalid_json_400(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/rooms/gl-k", data="not json")
        assert resp.status == 400


async def test_hue_error_envelope_502(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.put_errors = [{"description": "device unreachable"}]
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 502
        assert "device unreachable" in (await resp.json())["error"]


async def test_hue_http_error_502(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.put_status = 403
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 502


async def test_hue_read_failure_502(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.get_status = 500
        resp = await env.client.get("/api/rooms")
        assert resp.status == 502
        assert KEY not in await resp.text()


async def test_links_reread_each_request(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        first = await (await env.client.get("/api/links")).json()
        assert first == [{"title": "Nav", "url": "https://music.example.org"}]
        env.links.write_text(json.dumps([{"title": "Cam", "url": "http://cam.lan", "icon": "C"}]))
        assert await (await env.client.get("/api/links")).json() == [
            {"title": "Cam", "url": "http://cam.lan", "icon": "C"}
        ]


async def test_links_missing_file_is_empty(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.links.unlink()
        resp = await env.client.get("/api/links")
        assert resp.status == 200 and await resp.json() == []


@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"title": "x"}',
        '[{"title": "x", "url": "javascript:alert(1)"}]',
        '[{"url": "https://a.example"}]',
        '["x"]',
        '[{"title": "x", "url": "https://a.example", "open": "window"}]',
        '[{"title": "x", "url": "https://a.example", "open": true}]',
        '[{"title": "x", "url": "https://a.example", "open": ""}]',
    ],
)
async def test_links_invalid_500(tmp_path: Path, content: str) -> None:
    async with rig(tmp_path) as env:
        env.links.write_text(content)
        resp = await env.client.get("/api/links")
        assert resp.status == 500
        assert "error" in await resp.json()


async def test_links_open_mode_passes_through(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.links.write_text(
            json.dumps(
                [
                    {"title": "A", "url": "https://a.example", "open": "frame"},
                    {"title": "B", "url": "https://b.example", "open": "tab"},
                    {"title": "C", "url": "https://c.example"},
                ]
            )
        )
        data = await (await env.client.get("/api/links")).json()
        assert [d.get("open") for d in data] == ["frame", "tab", None]


def test_settings_defaults() -> None:
    s = load_settings({"KIOSK_HUE_HOST": "1.2.3.4", "KIOSK_HUE_APP_KEY": "topsecretkey"})
    assert s.hue_base_url == "https://1.2.3.4"
    assert s.port == 8080 and s.log_level == "info"
    assert s.links_file == Path("/config/links.json")
    assert "topsecretkey" not in repr(s)


@pytest.mark.parametrize(
    "env_vars, expected",
    [
        ({"KIOSK_HUE_APP_KEY": "k"}, "KIOSK_HUE_HOST"),
        ({"KIOSK_HUE_HOST": "h"}, "KIOSK_HUE_APP_KEY"),
        ({"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": " "}, "KIOSK_HUE_APP_KEY"),
        ({"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k", "KIOSK_PORT": "abc"}, "KIOSK_PORT"),
        ({"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k", "KIOSK_LOG_LEVEL": "loud"}, "LOG_LEVEL"),
    ],
)
def test_settings_errors(env_vars: dict[str, str], expected: str) -> None:
    with pytest.raises(ConfigError, match=expected):
        load_settings(env_vars)


def test_main_fails_fast_without_env(capsys: pytest.CaptureFixture[str]) -> None:
    from kiosk.app import main

    assert main({}) == 2
    err = capsys.readouterr().err
    assert "KIOSK_HUE_HOST" in err and "KIOSK_HUE_APP_KEY" in err
