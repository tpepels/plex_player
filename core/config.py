"""Configuration parsing and validation for Plex LCD.

Design assumptions:
- Environment variables are the single configuration source at runtime.
- Parsing is permissive (falls back to defaults), while validation reports all issues at once.
- This module performs no side effects beyond reading environment and local filesystem checks.
"""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from core.constants import (
    DEFAULT_BUTTON_BOUNCE_TIME,
    DEFAULT_BUTTON_LABEL_NEXT_Y_PERCENT,
    DEFAULT_BUTTON_LABEL_PLAY_Y_PERCENT,
    DEFAULT_BUTTON_LABEL_STOP_Y_PERCENT,
    DEFAULT_BUTTON_NEXT_PIN,
    DEFAULT_BUTTON_PLAY_PAUSE_PIN,
    DEFAULT_BUTTON_STOP_PIN,
    DEFAULT_DISPLAY_X_SHIFT,
    DEFAULT_FB_DEVICE,
    DEFAULT_HEIGHT,
    DEFAULT_NO_TRACK_GRACE_SECONDS,
    DEFAULT_PLAYER_NAME,
    DEFAULT_PLEX_SERVER,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PROGRESS_UPDATE_SECONDS,
    DEFAULT_TIMEZONE,
    DEFAULT_WEATHER_REFRESH_SECONDS,
    DEFAULT_WIDTH,
    TRUTHY_ENV_VALUES,
)


@dataclass
class Config:
    """Runtime configuration parsed from environment variables."""

    plex_server: str
    plex_token: str
    player_name: str
    latitude: float
    longitude: float
    timezone: str
    location_name: str
    fb_device: str
    width: int
    height: int
    buttons_enabled: bool
    button_play_pause_pin: int
    button_stop_pin: int
    button_next_pin: int
    button_bounce_time: float
    button_label_play_y_percent: int
    button_label_stop_y_percent: int
    button_label_next_y_percent: int
    poll_seconds: int
    weather_refresh_seconds: int
    progress_update_seconds: int
    no_track_grace_seconds: float
    display_x_shift: int
    debug_logging: bool

    @classmethod
    def from_env(cls, *, button_available: bool) -> tuple["Config", list[str]]:
        """Build and validate config from process environment.

        Assumptions:
        - Defaults are chosen for a Raspberry Pi + 320x240 framebuffer setup.
        - Validation errors are accumulated to improve setup UX.
        - `button_available` is injected by caller so this module stays hardware/library agnostic.
        """

        errors: list[str] = []

        def getenv(name: str, default: str) -> str:
            return os.environ.get(name, default)

        def parse_float(name: str, default: str) -> float:
            raw = getenv(name, default).strip()
            try:
                return float(raw)
            except (TypeError, ValueError):
                errors.append(f"{name} must be a valid number")
                return float(default)

        def parse_int(name: str, default: str) -> int:
            raw = getenv(name, default).strip()
            try:
                return int(raw)
            except (TypeError, ValueError):
                errors.append(f"{name} must be an integer")
                return int(default)

        def parse_bool(name: str, default: str) -> bool:
            raw = getenv(name, default).strip().lower()
            return raw in TRUTHY_ENV_VALUES

        cfg = cls(
            plex_server=getenv("PLEX_SERVER", DEFAULT_PLEX_SERVER).strip().rstrip("/"),
            plex_token=getenv("PLEX_TOKEN", "").strip(),
            player_name=getenv("PLAYER_NAME", DEFAULT_PLAYER_NAME).strip(),
            latitude=parse_float("LATITUDE", "0.0000"),
            longitude=parse_float("LONGITUDE", "0.0000"),
            timezone=getenv("TIMEZONE", DEFAULT_TIMEZONE).strip(),
            location_name=getenv("LOCATION_NAME", "").strip(),
            fb_device=getenv("FB_DEVICE", DEFAULT_FB_DEVICE).strip(),
            width=parse_int("WIDTH", str(DEFAULT_WIDTH)),
            height=parse_int("HEIGHT", str(DEFAULT_HEIGHT)),
            buttons_enabled=parse_bool("BUTTONS_ENABLED", "0"),
            button_play_pause_pin=parse_int("BUTTON_PLAY_PAUSE_PIN", str(DEFAULT_BUTTON_PLAY_PAUSE_PIN)),
            button_stop_pin=parse_int("BUTTON_STOP_PIN", str(DEFAULT_BUTTON_STOP_PIN)),
            button_next_pin=parse_int("BUTTON_NEXT_PIN", str(DEFAULT_BUTTON_NEXT_PIN)),
            button_bounce_time=parse_float("BUTTON_BOUNCE_TIME", str(DEFAULT_BUTTON_BOUNCE_TIME)),
            button_label_play_y_percent=parse_int("BUTTON_LABEL_PLAY_Y_PERCENT", str(DEFAULT_BUTTON_LABEL_PLAY_Y_PERCENT)),
            button_label_stop_y_percent=parse_int("BUTTON_LABEL_STOP_Y_PERCENT", str(DEFAULT_BUTTON_LABEL_STOP_Y_PERCENT)),
            button_label_next_y_percent=parse_int("BUTTON_LABEL_NEXT_Y_PERCENT", str(DEFAULT_BUTTON_LABEL_NEXT_Y_PERCENT)),
            poll_seconds=parse_int("POLL_SECONDS", str(DEFAULT_POLL_SECONDS)),
            weather_refresh_seconds=parse_int("WEATHER_REFRESH_SECONDS", str(DEFAULT_WEATHER_REFRESH_SECONDS)),
            progress_update_seconds=parse_int("PROGRESS_UPDATE_SECONDS", str(DEFAULT_PROGRESS_UPDATE_SECONDS)),
            no_track_grace_seconds=parse_float("NO_TRACK_GRACE_SECONDS", str(DEFAULT_NO_TRACK_GRACE_SECONDS)),
            display_x_shift=parse_int("DISPLAY_X_SHIFT", str(DEFAULT_DISPLAY_X_SHIFT)),
            debug_logging=parse_bool("DEBUG_LOGGING", "0"),
        )

        if not cfg.plex_token:
            errors.append("PLEX_TOKEN not set or empty")
        if cfg.width <= 0:
            errors.append("WIDTH must be > 0")
        if cfg.height <= 0:
            errors.append("HEIGHT must be > 0")
        if cfg.poll_seconds < 1:
            errors.append("POLL_SECONDS must be >= 1")
        if cfg.weather_refresh_seconds < 60:
            errors.append("WEATHER_REFRESH_SECONDS must be >= 60")
        if cfg.progress_update_seconds < 1:
            errors.append("PROGRESS_UPDATE_SECONDS must be >= 1")
        if cfg.no_track_grace_seconds < 0:
            errors.append("NO_TRACK_GRACE_SECONDS must be >= 0")
        if abs(cfg.display_x_shift) >= max(1, cfg.width):
            errors.append("DISPLAY_X_SHIFT must be smaller than WIDTH")

        for label_name, label_percent in (
            ("BUTTON_LABEL_PLAY_Y_PERCENT", cfg.button_label_play_y_percent),
            ("BUTTON_LABEL_STOP_Y_PERCENT", cfg.button_label_stop_y_percent),
            ("BUTTON_LABEL_NEXT_Y_PERCENT", cfg.button_label_next_y_percent),
        ):
            if not 0 <= label_percent <= 100:
                errors.append(f"{label_name} must be between 0 and 100")

        if not os.path.exists(cfg.fb_device):
            errors.append(f"FB_DEVICE '{cfg.fb_device}' does not exist")
        elif not os.access(cfg.fb_device, os.W_OK):
            errors.append(f"FB_DEVICE '{cfg.fb_device}' is not writable (need root or group membership)")

        if cfg.buttons_enabled and not button_available:
            errors.append("BUTTONS_ENABLED is set but gpiozero is not installed")

        try:
            ZoneInfo(cfg.timezone)
        except Exception:
            errors.append(f"TIMEZONE '{cfg.timezone}' is invalid")

        return cfg, errors
