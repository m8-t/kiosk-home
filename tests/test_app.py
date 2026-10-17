import asyncio
import json
import logging
import shutil
import socket
import ssl
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import kiosk
from kiosk.app import UNKNOWN_REFRESH_S, create_app
from kiosk.config import ConfigError, Settings, load_settings
from kiosk.hue import OVERVIEW_TTL_S, HueClient

KEY = "secret-app-key"
BRIDGE_ID = "001788fffe694a2c"


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
            "gl-all": {"id": "gl-all", "on": {"on": True}, "dimming": {"brightness": 40.0}},
        }
        self.bridge_home: list[dict[str, Any]] = [
            {
                "id": "bh-1",
                "type": "bridge_home",
                "services": [{"rtype": "grouped_light", "rid": "gl-all"}],
            }
        ]
        self.bridge_home_gets = 0
        self.room_gets = 0
        self.light_gets = 0
        self.delay = 0.0
        self.gate: asyncio.Event | None = None
        self.puts: list[tuple[str, dict[str, Any]]] = []
        self.keys: list[str | None] = []
        self.put_status = 200
        self.put_errors: list[dict[str, str]] = []
        self.get_status = 200
        self.app = web.Application()
        self.app.router.add_get("/clip/v2/resource/room", self.get_rooms)
        self.app.router.add_get("/clip/v2/resource/bridge_home", self.get_bridge_home)
        self.app.router.add_get("/clip/v2/resource/grouped_light", self.get_lights)
        self.app.router.add_put("/clip/v2/resource/grouped_light/{id}", self.put_light)

    async def slow(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.gate is not None:
            await self.gate.wait()

    async def get_rooms(self, request: web.Request) -> web.Response:
        self.room_gets += 1
        self.keys.append(request.headers.get("hue-application-key"))
        await self.slow()
        return web.json_response({"errors": [], "data": self.rooms}, status=self.get_status)

    async def get_bridge_home(self, request: web.Request) -> web.Response:
        self.bridge_home_gets += 1
        return web.json_response({"errors": [], "data": self.bridge_home}, status=self.get_status)

    async def get_lights(self, request: web.Request) -> web.Response:
        self.light_gets += 1
        await self.slow()
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
async def rig(
    tmp_path: Path, *, hue_tls: ssl.SSLContext | None = None, **overrides: Any
) -> AsyncIterator[Env]:
    hue = FakeHue()
    links = tmp_path / "links.json"
    links.write_text(json.dumps([{"title": "Nav", "url": "https://music.example.org"}]))
    hue_server = TestServer(hue.app)
    await hue_server.start_server(ssl=hue_tls)
    async with hue_server:
        settings = Settings(
            hue_host=f"{hue_server.scheme}://127.0.0.1:{hue_server.port}",
            hue_app_key=KEY,
            links_file=links,
            **overrides,
        )
        async with TestClient(TestServer(create_app(settings))) as client:
            yield Env(client, hue, links)


async def test_rooms_join_and_sort(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.get("/api/rooms")
        assert resp.status == 200
        assert await resp.json() == {
            "all": {"id": "gl-all", "on": True},
            "rooms": [
                {"id": "gl-b", "name": "bedroom", "on": False, "brightness": None},
                {"id": "gl-k", "name": "Kitchen", "on": True, "brightness": 54.3},
            ],
        }
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
        resp = await env.client.post(
            "/api/rooms/gl-k", data="not json", headers={"Content-Type": "application/json"}
        )
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
        '[{"title": "x", "url": "https://a.example", "color": "red"}]',
        '[{"title": "x", "url": "https://a.example", "color": "#12"}]',
        '[{"title": "x", "url": "https://a.example", "color": "#12345g"}]',
        '[{"title": "x", "url": "https://a.example", "color": 5}]',
        '[{"title": "x", "url": "https://a.example", "color": "#a855f7;x"}]',
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


async def test_links_color_passes_through(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.links.write_text(
            json.dumps(
                [
                    {"title": "A", "url": "https://a.example", "color": "#a855f7"},
                    {"title": "B", "url": "https://b.example", "color": "#0af"},
                    {"title": "C", "url": "https://c.example"},
                ]
            )
        )
        data = await (await env.client.get("/api/links")).json()
        assert [d.get("color") for d in data] == ["#a855f7", "#0af", None]


@pytest.mark.parametrize("on", [True, False])
async def test_all_switch_single_put(tmp_path: Path, on: bool) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/all", json={"on": on})
        assert resp.status == 200 and await resp.json() == {"on": on}
        assert env.hue.puts == [("gl-all", {"on": {"on": on}})]
        await env.client.post("/api/all", json={"on": on})
        assert env.hue.bridge_home_gets == 1


async def test_all_missing_bridge_home(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.bridge_home = []
        data = await (await env.client.get("/api/rooms")).json()
        assert data["all"] is None and len(data["rooms"]) == 2
        resp = await env.client.post("/api/all", json={"on": True})
        assert resp.status == 404
        assert env.hue.puts == []


@pytest.mark.parametrize("payload", [{}, {"on": 1}, {"on": True, "brightness": 5}, [True]])
async def test_all_bad_body_400(tmp_path: Path, payload: Any) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post("/api/all", json=payload)
        assert resp.status == 400
        assert env.hue.puts == []


async def test_all_put_failure_502(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        env.hue.put_status = 500
        assert (await env.client.post("/api/all", json={"on": False})).status == 502


def test_lights_default_true_and_parsing() -> None:
    base = {"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k"}
    assert load_settings(base).lights is True
    for raw in ["true", "TRUE", "1", "yes", "Yes", " true "]:
        assert load_settings({**base, "KIOSK_LIGHTS": raw}).lights is True
    for raw in ["false", "False", "0", "no", "NO"]:
        assert load_settings({**base, "KIOSK_LIGHTS": raw}).lights is False


def test_lights_invalid_value() -> None:
    with pytest.raises(ConfigError, match="KIOSK_LIGHTS"):
        load_settings({"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k", "KIOSK_LIGHTS": "maybe"})


def test_hue_vars_optional_when_lights_disabled() -> None:
    s = load_settings({"KIOSK_LIGHTS": "false"})
    assert s.lights is False and s.hue_host == "" and s.hue_app_key == ""
    ignored = load_settings({"KIOSK_LIGHTS": "0", "KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k"})
    assert ignored.lights is False and ignored.hue_host == ""


def test_hue_vars_still_required_when_lights_enabled() -> None:
    with pytest.raises(ConfigError, match="KIOSK_HUE_HOST"):
        load_settings({"KIOSK_LIGHTS": "true"})
    with pytest.raises(ConfigError, match="KIOSK_HUE_HOST"):
        load_settings({})


@asynccontextmanager
async def lights_off_client(tmp_path: Path) -> AsyncIterator[TestClient]:
    links = tmp_path / "links.json"
    links.write_text(json.dumps([{"title": "Nav", "url": "https://music.example.org"}]))
    async with TestClient(TestServer(create_app(Settings(lights=False, links_file=links)))) as c:
        yield c


async def test_lights_disabled_endpoints_404(tmp_path: Path) -> None:
    async with lights_off_client(tmp_path) as client:
        for resp in [
            await client.get("/api/rooms"),
            await client.post("/api/rooms/gl-k", json={"on": True}),
            await client.post("/api/all", json={"on": True}),
        ]:
            assert resp.status == 404
            assert await resp.json() == {"error": "lights disabled"}
        assert (await client.get("/api/links")).status == 200
        assert (await client.get("/healthz")).status == 200
        assert (await client.get("/")).status == 200


async def test_config_endpoint(tmp_path: Path) -> None:
    async with lights_off_client(tmp_path) as client:
        assert await (await client.get("/api/config")).json() == {"lights": False}
    async with rig(tmp_path) as env:
        assert await (await env.client.get("/api/config")).json() == {"lights": True}
        assert isinstance(await (await env.client.get("/api/links")).json(), list)


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


POSTS = [("/api/rooms/gl-k", {"on": False}), ("/api/all", {"on": False})]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def wait_for_room_gets(hue: FakeHue, count: int) -> None:
    async with asyncio.timeout(5):
        while hue.room_gets < count:
            await asyncio.sleep(0.01)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr("kiosk.app.monotonic", fake)
    monkeypatch.setattr("kiosk.hue.monotonic", fake)
    return fake


@pytest.mark.parametrize("path, payload", POSTS)
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "text/plain; charset=UTF-8", "application/x-www-form-urlencoded", "text/json"],
)
async def test_post_needs_json_content_type(
    tmp_path: Path, path: str, payload: Any, content_type: str
) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post(
            path, data=json.dumps(payload), headers={"Content-Type": content_type}
        )
        assert resp.status == 415
        assert await resp.json() == {"error": "content type must be application/json"}
        assert env.hue.keys == [] and env.hue.puts == []


async def test_post_without_content_type_415(tmp_path: Path) -> None:
    async with rig(tmp_path) as env:
        for path, payload in POSTS:
            resp = await env.client.post(
                path, data=json.dumps(payload).encode(), skip_auto_headers=["Content-Type"]
            )
            assert resp.status == 415
        assert env.hue.puts == []


@pytest.mark.parametrize(
    "content_type", ["application/json", "application/json; charset=utf-8", "Application/JSON"]
)
async def test_json_content_type_variants_pass(tmp_path: Path, content_type: str) -> None:
    async with rig(tmp_path) as env:
        for path, payload in POSTS:
            resp = await env.client.post(
                path, data=json.dumps(payload), headers={"Content-Type": content_type}
            )
            assert resp.status == 200
        assert len(env.hue.puts) == 2


@pytest.mark.parametrize("path, payload", POSTS)
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://[::1"},
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://evil.example"},
    ],
)
async def test_cross_site_post_403(
    tmp_path: Path, path: str, payload: Any, headers: dict[str, str]
) -> None:
    async with rig(tmp_path) as env:
        resp = await env.client.post(path, json=payload, headers=headers)
        assert resp.status == 403
        assert await resp.json() == {"error": "cross-site request"}
        assert env.hue.keys == [] and env.hue.puts == []


@pytest.mark.parametrize("path, payload", POSTS)
async def test_origin_with_other_port_403(tmp_path: Path, path: str, payload: Any) -> None:
    async with rig(tmp_path) as env:
        origin = f"http://{env.client.host}:{env.client.port + 1}"
        resp = await env.client.post(path, json=payload, headers={"Origin": origin})
        assert resp.status == 403
        assert env.hue.puts == []


@pytest.mark.parametrize("path, payload", POSTS)
async def test_same_origin_post_passes(tmp_path: Path, path: str, payload: Any) -> None:
    async with rig(tmp_path) as env:
        origin = f"http://{env.client.host}:{env.client.port}"
        for headers in (
            {"Sec-Fetch-Site": "same-origin", "Origin": origin},
            {"Sec-Fetch-Site": "same-origin"},
            {"Origin": origin},
        ):
            resp = await env.client.post(path, json=payload, headers=headers)
            assert resp.status == 200
        assert len(env.hue.puts) == 3


def test_bridge_id_parsing() -> None:
    base = {"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k"}
    assert load_settings(base).hue_bridge_id == ""
    assert load_settings({**base, "KIOSK_HUE_BRIDGE_ID": BRIDGE_ID}).hue_bridge_id == BRIDGE_ID
    padded = {**base, "KIOSK_HUE_BRIDGE_ID": f" {BRIDGE_ID.upper()} "}
    assert load_settings(padded).hue_bridge_id == BRIDGE_ID
    assert load_settings({**base, "KIOSK_HUE_BRIDGE_ID": "  "}).hue_bridge_id == ""


@pytest.mark.parametrize(
    "raw",
    [
        BRIDGE_ID[:-1],
        BRIDGE_ID + "0",
        "001788fffe694a2g",
        "00:17:88:ff:fe:69:4a:2c",
        "bridge",
        "٠" * 16,
    ],
)
def test_bridge_id_invalid(raw: str) -> None:
    env = {"KIOSK_HUE_HOST": "h", "KIOSK_HUE_APP_KEY": "k", "KIOSK_HUE_BRIDGE_ID": raw}
    with pytest.raises(ConfigError, match="KIOSK_HUE_BRIDGE_ID"):
        load_settings(env)


def test_bridge_id_ignored_when_lights_disabled() -> None:
    env = {"KIOSK_LIGHTS": "false", "KIOSK_HUE_BRIDGE_ID": "nonsense"}
    assert load_settings(env).hue_bridge_id == ""


def test_default_hue_ca_file_is_the_bundled_one() -> None:
    bundled = Path(kiosk.__file__).resolve().parent / "hue_ca.pem"
    assert Settings().hue_ca_file == bundled
    assert load_settings({"KIOSK_LIGHTS": "false"}).hue_ca_file == bundled


@dataclass(frozen=True)
class Pki:
    ca_file: Path
    server: ssl.SSLContext
    foreign_server: ssl.SSLContext


EC_KEY = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes")
CA_EXTENSIONS = (
    "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign\nsubjectKeyIdentifier=hash\n"
)
# CN only and no subjectAltName, like the certificate the real bridge presents.
BRIDGE_EXTENSIONS = (
    "basicConstraints=critical,CA:FALSE\n"
    "keyUsage=critical,digitalSignature\n"
    "extendedKeyUsage=serverAuth\n"
    "subjectKeyIdentifier=hash\n"
    "authorityKeyIdentifier=keyid\n"
)


def openssl(*args: str | Path) -> None:
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)


def issue(
    directory: Path, name: str, common_name: str, extensions: str, ca: tuple[Path, Path] | None = None
) -> tuple[Path, Path]:
    """Create a key and a certificate for CN=common_name, signed by ca (key, cert) or itself."""
    base = directory / name
    conf, key, csr, cert, ext = map(base.with_suffix, (".cnf", ".key", ".csr", ".pem", ".ext"))
    conf.write_text("[req]\ndistinguished_name = dn\n[dn]\n")
    ext.write_text(extensions)
    subject = f"/CN={common_name}"
    openssl("req", "-new", "-config", conf, *EC_KEY, "-keyout", key, "-out", csr, "-subj", subject)
    signer = ("-CA", ca[1], "-CAkey", ca[0], "-CAcreateserial") if ca else ("-signkey", key)
    openssl("x509", "-req", "-in", csr, *signer, "-out", cert, "-days", "2", "-extfile", ext)
    return key, cert


def server_context(key: Path, cert: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    return context


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    directory = tmp_path_factory.mktemp("pki")
    trusted = issue(directory, "ca1", "ca1", CA_EXTENSIONS)
    other = issue(directory, "ca2", "ca2", CA_EXTENSIONS)
    leaf = issue(directory, "leaf1", BRIDGE_ID, BRIDGE_EXTENSIONS, trusted)
    foreign_leaf = issue(directory, "leaf2", BRIDGE_ID, BRIDGE_EXTENSIONS, other)
    return Pki(
        ca_file=trusted[1],
        server=server_context(*leaf),
        foreign_server=server_context(*foreign_leaf),
    )


def record_hue_requests(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    original = aiohttp.ClientSession.request

    def spy(self: aiohttp.ClientSession, method: str, url: Any, **kwargs: Any) -> Any:
        if "hue-application-key" in (kwargs.get("headers") or {}):
            calls.append((method, kwargs))
        return original(self, method, url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "request", spy)
    return calls


async def test_bridge_certificate_is_verified_for_the_bridge_id(
    tmp_path: Path, pki: Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = record_hue_requests(monkeypatch)
    async with rig(
        tmp_path, hue_tls=pki.server, hue_bridge_id=BRIDGE_ID.upper(), hue_ca_file=pki.ca_file
    ) as env:
        resp = await env.client.get("/api/rooms")
        assert resp.status == 200
        assert [r["name"] for r in (await resp.json())["rooms"]] == ["bedroom", "Kitchen"]
        assert (await env.client.post("/api/rooms/gl-k", json={"on": False})).status == 200
        assert set(env.hue.keys) == {KEY}
    assert {method for method, _ in calls} == {"GET", "PUT"}
    context = calls[0][1]["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert all(kwargs["ssl"] is context for _, kwargs in calls)
    assert all(kwargs["server_hostname"] == BRIDGE_ID for _, kwargs in calls)


@pytest.mark.parametrize(
    "presented, bridge_id",
    [("server", "001788fffe000000"), ("foreign_server", BRIDGE_ID)],
    ids=["wrong-id", "foreign-ca"],
)
async def test_certificate_failure_is_502_and_sends_no_key(
    tmp_path: Path, pki: Pki, caplog: pytest.LogCaptureFixture, presented: str, bridge_id: str
) -> None:
    caplog.set_level(logging.WARNING)
    expected = {"error": "Hue bridge certificate check failed, check KIOSK_HUE_BRIDGE_ID"}
    async with rig(
        tmp_path,
        hue_tls=getattr(pki, presented),
        hue_bridge_id=bridge_id,
        hue_ca_file=pki.ca_file,
    ) as env:
        resp = await env.client.get("/api/rooms")
        assert resp.status == 502 and await resp.json() == expected
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 502 and await resp.json() == expected
        assert env.hue.keys == [] and env.hue.puts == []
    assert "ClientConnectorCertificateError" in caplog.text
    assert KEY not in caplog.text


async def test_certificate_is_not_checked_without_bridge_id(
    tmp_path: Path, pki: Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = record_hue_requests(monkeypatch)
    async with rig(tmp_path, hue_tls=pki.foreign_server) as env:
        assert (await env.client.get("/api/rooms")).status == 200
        assert set(env.hue.keys) == {KEY}
    assert calls
    assert all(kwargs["ssl"] is False for _, kwargs in calls)
    assert all(kwargs["server_hostname"] is None for _, kwargs in calls)


async def test_plain_http_hue_never_uses_tls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = record_hue_requests(monkeypatch)
    async with rig(tmp_path, hue_bridge_id=BRIDGE_ID, hue_ca_file=tmp_path / "missing.pem") as env:
        assert (await env.client.get("/api/rooms")).status == 200
    assert calls
    assert all(kwargs["ssl"] is False for _, kwargs in calls)
    assert all(kwargs["server_hostname"] is None for _, kwargs in calls)


async def test_tls_failure_without_bridge_id_stays_unreachable(tmp_path: Path) -> None:
    async with TestServer(FakeHue().app) as plain_http:
        settings = Settings(
            hue_host=f"https://127.0.0.1:{plain_http.port}",
            hue_app_key=KEY,
            links_file=tmp_path / "links.json",
        )
        async with TestClient(TestServer(create_app(settings))) as client:
            resp = await client.get("/api/rooms")
            assert resp.status == 502
            assert await resp.json() == {"error": "Hue bridge unreachable"}


async def test_connection_refused_stays_unreachable(tmp_path: Path, pki: Pki) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings(
        hue_host=f"https://127.0.0.1:{port}",
        hue_app_key=KEY,
        links_file=tmp_path / "links.json",
        hue_bridge_id=BRIDGE_ID,
        hue_ca_file=pki.ca_file,
    )
    async with TestClient(TestServer(create_app(settings))) as client:
        resp = await client.get("/api/rooms")
        assert resp.status == 502
        assert await resp.json() == {"error": "Hue bridge unreachable"}


@pytest.mark.parametrize("content", [None, "not a certificate"])
async def test_unreadable_ca_file_fails_at_startup(tmp_path: Path, content: str | None) -> None:
    ca_file = tmp_path / "broken.pem"
    if content is not None:
        ca_file.write_text(content)
    settings = Settings(
        hue_host="https://127.0.0.1:1",
        hue_app_key=KEY,
        links_file=tmp_path / "links.json",
        hue_bridge_id=BRIDGE_ID,
        hue_ca_file=ca_file,
    )
    client = TestClient(TestServer(create_app(settings)))
    try:
        with pytest.raises(ConfigError, match="broken.pem"):
            await client.start_server()
    finally:
        await client.close()


def test_startup_warns_when_certificate_is_not_verified(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    create_app(Settings(hue_host="h", hue_app_key=KEY))
    assert [r.name for r in caplog.records] == ["kiosk"]
    assert "not verified" in caplog.records[0].getMessage()
    assert KEY not in caplog.text
    caplog.clear()
    create_app(Settings(hue_host="h", hue_app_key=KEY, hue_bridge_id=BRIDGE_ID))
    create_app(Settings(lights=False))
    assert caplog.records == []


async def test_unknown_ids_refresh_at_most_once_per_window(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        for room_id in ("nope-1", "nope-2"):
            resp = await env.client.post(f"/api/rooms/{room_id}", json={"on": True})
            assert resp.status == 404
            assert await resp.json() == {"error": "unknown room"}
        assert env.hue.room_gets == 1
        clock.advance(UNKNOWN_REFRESH_S / 2)
        assert (await env.client.post("/api/rooms/nope-3", json={"on": True})).status == 404
        assert env.hue.room_gets == 1
        clock.advance(UNKNOWN_REFRESH_S / 2)
        assert (await env.client.post("/api/rooms/nope-4", json={"on": True})).status == 404
        assert env.hue.room_gets == 2
        assert env.hue.puts == []


async def test_new_room_is_found_after_the_window(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        assert (await env.client.post("/api/rooms/gl-n", json={"on": True})).status == 404
        env.hue.rooms.append(
            {
                "id": "room-n",
                "metadata": {"name": "New"},
                "services": [{"rtype": "grouped_light", "rid": "gl-n"}],
            }
        )
        env.hue.lights["gl-n"] = {"id": "gl-n", "on": {"on": False}}
        assert (await env.client.post("/api/rooms/gl-n", json={"on": True})).status == 404
        clock.advance(UNKNOWN_REFRESH_S)
        assert (await env.client.post("/api/rooms/gl-n", json={"on": True})).status == 200
        assert env.hue.puts == [("gl-n", {"on": {"on": True}})]


async def test_failed_refresh_keeps_known_ids(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        env.hue.get_status = 500
        resp = await env.client.post("/api/rooms/nope-1", json={"on": True})
        assert resp.status == 502
        env.hue.get_status = 200
        reads = env.hue.room_gets
        resp = await env.client.post("/api/rooms/gl-k", json={"on": False})
        assert resp.status == 200
        assert env.hue.room_gets == reads
        assert env.hue.puts == [("gl-k", {"on": {"on": False}})]
        resp = await env.client.post("/api/rooms/nope-2", json={"on": True})
        assert resp.status == 404
        assert env.hue.room_gets == reads


async def test_concurrent_polls_share_one_hue_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kiosk.hue.OVERVIEW_TTL_S", 0)
    async with rig(tmp_path) as env:
        env.hue.delay = 0.2
        first, second = await asyncio.gather(
            env.client.get("/api/rooms"), env.client.get("/api/rooms")
        )
        assert first.status == second.status == 200
        assert await first.json() == await second.json()
        assert (env.hue.room_gets, env.hue.light_gets, env.hue.bridge_home_gets) == (1, 1, 1)


async def test_cancelled_caller_does_not_cancel_the_shared_read() -> None:
    hue = FakeHue()
    hue.gate = asyncio.Event()
    async with TestServer(hue.app) as server, aiohttp.ClientSession() as session:
        client = HueClient(f"http://127.0.0.1:{server.port}", KEY, session)
        first = asyncio.ensure_future(client.overview())
        second = asyncio.ensure_future(client.overview())
        await wait_for_room_gets(hue, 1)
        first.cancel()
        hue.gate.set()
        assert len((await second)["rooms"]) == 2
        assert hue.room_gets == 1


async def test_overview_is_cached_for_the_ttl(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        first = await (await env.client.get("/api/rooms")).json()
        clock.advance(OVERVIEW_TTL_S / 2)
        assert await (await env.client.get("/api/rooms")).json() == first
        assert (env.hue.room_gets, env.hue.light_gets) == (1, 1)
        clock.advance(OVERVIEW_TTL_S / 2)
        assert await (await env.client.get("/api/rooms")).json() == first
        assert (env.hue.room_gets, env.hue.light_gets) == (2, 2)


@pytest.mark.parametrize("path, payload", POSTS)
async def test_successful_put_invalidates_the_overview(
    tmp_path: Path, clock: Clock, path: str, payload: Any
) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        assert (await env.client.post(path, json=payload)).status == 200
        await env.client.get("/api/rooms")
        assert env.hue.room_gets == 2


async def test_failed_put_keeps_the_overview(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        env.hue.put_status = 500
        assert (await env.client.post("/api/rooms/gl-k", json={"on": False})).status == 502
        await env.client.get("/api/rooms")
        assert env.hue.room_gets == 1


async def test_failed_read_is_not_cached(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        env.hue.get_status = 500
        assert (await env.client.get("/api/rooms")).status == 502
        env.hue.get_status = 200
        resp = await env.client.get("/api/rooms")
        assert resp.status == 200
        assert len((await resp.json())["rooms"]) == 2
        assert env.hue.room_gets == 2


async def test_read_in_flight_during_a_put_is_not_cached(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        clock.advance(OVERVIEW_TTL_S)
        env.hue.gate = asyncio.Event()
        poll = asyncio.ensure_future(env.client.get("/api/rooms"))
        await wait_for_room_gets(env.hue, 2)
        assert (await env.client.post("/api/rooms/gl-k", json={"on": False})).status == 200
        env.hue.gate.set()
        assert (await poll).status == 200
        await env.client.get("/api/rooms")
        assert env.hue.room_gets == 3


async def test_poll_after_a_put_does_not_join_an_older_read(tmp_path: Path, clock: Clock) -> None:
    async with rig(tmp_path) as env:
        await env.client.get("/api/rooms")
        clock.advance(OVERVIEW_TTL_S)
        env.hue.gate = asyncio.Event()
        before = asyncio.ensure_future(env.client.get("/api/rooms"))
        await wait_for_room_gets(env.hue, 2)
        assert (await env.client.post("/api/rooms/gl-k", json={"on": False})).status == 200
        after = asyncio.ensure_future(env.client.get("/api/rooms"))
        await wait_for_room_gets(env.hue, 3)
        env.hue.gate.set()
        assert (await before).status == (await after).status == 200
        await env.client.get("/api/rooms")
        assert env.hue.room_gets == 3
