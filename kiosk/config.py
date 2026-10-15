"""Settings read from KIOSK_* environment variables."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    hue_host: str
    hue_app_key: str = field(repr=False)
    links_file: Path = Path("/config/links.json")
    port: int = 8080
    log_level: str = "info"

    @property
    def hue_base_url(self) -> str:
        host = self.hue_host.rstrip("/")
        if host.startswith(("http://", "https://")):
            return host
        return f"https://{host}"


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [
        name for name in ("KIOSK_HUE_HOST", "KIOSK_HUE_APP_KEY") if not env.get(name, "").strip()
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
        hue_host=env["KIOSK_HUE_HOST"].strip(),
        hue_app_key=env["KIOSK_HUE_APP_KEY"].strip(),
        links_file=Path(env.get("KIOSK_LINKS_FILE", "").strip() or "/config/links.json"),
        port=port,
        log_level=level,
    )
