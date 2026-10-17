"""Settings read from KIOSK_* environment variables."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HUE_CA_FILE = Path(__file__).resolve().parent / "hue_ca.pem"


class ConfigError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    hue_host: str = ""
    hue_app_key: str = field(default="", repr=False)
    hue_bridge_id: str = ""
    hue_ca_file: Path = DEFAULT_HUE_CA_FILE
    lights: bool = True
    links_file: Path = Path("/config/links.json")
    port: int = 8080
    log_level: str = "info"

    @property
    def hue_base_url(self) -> str:
        host = self.hue_host.rstrip("/")
        if host.startswith(("http://", "https://")):
            return host
        return f"https://{host}"


_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}
_BRIDGE_ID = re.compile(r"[0-9a-f]{16}")


def _parse_bool(name: str, raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false/1/0/yes/no, got {raw!r}")


def _parse_bridge_id(raw: str) -> str:
    value = raw.strip().lower()
    if value and not _BRIDGE_ID.fullmatch(value):
        raise ConfigError(f"KIOSK_HUE_BRIDGE_ID must be 16 hex characters, got {raw!r}")
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    lights = _parse_bool("KIOSK_LIGHTS", env.get("KIOSK_LIGHTS", ""), True)
    if lights:
        missing = [
            name
            for name in ("KIOSK_HUE_HOST", "KIOSK_HUE_APP_KEY")
            if not env.get(name, "").strip()
        ]
        if missing:
            raise ConfigError(f"missing required environment variable(s): {', '.join(missing)}")
    raw_port = env.get("KIOSK_PORT", "").strip() or "8080"
    try:
        port = int(raw_port)
    except ValueError:
        raise ConfigError(f"KIOSK_PORT must be a number, got {raw_port!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"KIOSK_PORT must be between 1 and 65535, got {port}")
    level = (env.get("KIOSK_LOG_LEVEL", "").strip() or "info").lower()
    if not isinstance(logging.getLevelName(level.upper()), int):
        raise ConfigError(f"KIOSK_LOG_LEVEL is not a log level: {level!r}")
    return Settings(
        hue_host=env.get("KIOSK_HUE_HOST", "").strip() if lights else "",
        hue_app_key=env.get("KIOSK_HUE_APP_KEY", "").strip() if lights else "",
        hue_bridge_id=_parse_bridge_id(env.get("KIOSK_HUE_BRIDGE_ID", "")) if lights else "",
        lights=lights,
        links_file=Path(env.get("KIOSK_LINKS_FILE", "").strip() or "/config/links.json"),
        port=port,
        log_level=level,
    )
