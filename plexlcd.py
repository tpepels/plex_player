#!/usr/bin/env python3
"""Plex LCD application entrypoint and rendering loop.

Module responsibilities:
- Load and validate runtime configuration.
- Poll Plex/weather services and transform data into framebuffer images.
- Handle GPIO button events and dispatch playback commands.

Key runtime assumptions:
- App runs on Linux with writable framebuffer device (typically `/dev/fb1`).
- Plex sessions endpoint is reachable and includes configured player when active.
- UI must degrade gracefully: failed network/media calls should show placeholders, not crash loop.
"""

import os
import socket
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps
from core import (
    ButtonControllerConfig,
    Config,
    DisplayAdapterConfig,
    DisplayAdapterLogger,
    LoopState,
    PlaybackCollectorConfig,
    PlaybackCollectorDeps,
    PlexTrack,
    RuntimeState,
    TransitionMode,
    WeatherInfo,
    apply_button_rules,
    apply_transition_decision,
    collect_playback_snapshot,
    commit_idle_render_state,
    commit_playing_render_state,
    create_error_placeholder as display_create_error_placeholder,
    dispatch_playback_command,
    resolve_idle_render_plan,
    resolve_playing_render_plan,
    resolve_transition,
    resolve_wait_timeout,
    setup_gpio_buttons as setup_button_devices,
    should_poll_timeline,
    try_write_framebuffer,
    write_fallback_placeholder,
    DEFAULT_BUTTON_BOUNCE_TIME,
    DEFAULT_BUTTON_LABEL_NEXT_Y_PERCENT,
    DEFAULT_BUTTON_LABEL_PLAY_Y_PERCENT,
    DEFAULT_BUTTON_LABEL_STOP_Y_PERCENT,
    DEFAULT_BUTTON_NEXT_PIN,
    DEFAULT_BUTTON_PLAY_PAUSE_PIN,
    DEFAULT_BUTTON_STOP_PIN,
    DEFAULT_COMMAND_CONFIRM_SECONDS,
    DEFAULT_COVER_RETRY_SECONDS,
    DEFAULT_DISPLAY_X_SHIFT,
    DEFAULT_FB_DEVICE,
    DEFAULT_FONT_BOLD_CANDIDATES,
    DEFAULT_FONT_REGULAR_CANDIDATES,
    DEFAULT_FONT_SYMBOLS_CANDIDATES,
    DEFAULT_HEIGHT,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_NO_TRACK_GRACE_SECONDS,
    DEFAULT_PLAYER_NAME,
    DEFAULT_PLEX_SERVER,
    DEFAULT_POLL_SECONDS,
    DEFAULT_PROGRESS_UPDATE_SECONDS,
    DEFAULT_TIMEZONE,
    DEFAULT_TOAST_DURATION_SECONDS,
    DEFAULT_WEATHER_REFRESH_SECONDS,
    DEFAULT_WIDTH,
    TRUTHY_ENV_VALUES,
)
from services import (
    WEATHER_CODES,
    fetch_cover,
    fetch_player_timeline_state,
    fetch_sessions_json,
    fetch_weather,
    find_player_track,
    get_weather_symbol,
    playback_status_text,
    send_playback_command,
)
from zoneinfo import ZoneInfo

try:
    from gpiozero import Button
except Exception:
    Button = None


# Environment bootstrap helpers
def load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env file into process environment."""
    # Assumption: first readable candidate should win to avoid mixing partial configs.
    env_path = os.environ.get("PLEXLCD_ENV")
    if env_path:
        candidates = [env_path]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(script_dir, ".env"),
        ]

    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception as exc:
            print(f"[startup] Failed to read {candidate}: {exc}", file=sys.stderr)
        break


load_dotenv()


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Runtime defaults (overridden by validated Config in validate_startup)
# Assumption: these defaults are safe placeholders before validated config is applied.
PLEX_SERVER = env("PLEX_SERVER", DEFAULT_PLEX_SERVER).rstrip("/")
PLEX_TOKEN = env("PLEX_TOKEN", "")
PLAYER_NAME = env("PLAYER_NAME", DEFAULT_PLAYER_NAME)
LATITUDE = 0.0
LONGITUDE = 0.0
TIMEZONE = env("TIMEZONE", DEFAULT_TIMEZONE)
LOCATION_NAME = env("LOCATION_NAME", "").strip()
FB_DEVICE = env("FB_DEVICE", DEFAULT_FB_DEVICE)
WIDTH = DEFAULT_WIDTH
HEIGHT = DEFAULT_HEIGHT
BUTTONS_ENABLED = False
BUTTON_PLAY_PAUSE_PIN = DEFAULT_BUTTON_PLAY_PAUSE_PIN
BUTTON_STOP_PIN = DEFAULT_BUTTON_STOP_PIN
BUTTON_NEXT_PIN = DEFAULT_BUTTON_NEXT_PIN
BUTTON_BOUNCE_TIME = DEFAULT_BUTTON_BOUNCE_TIME
BUTTON_LABEL_PLAY_Y_PERCENT = DEFAULT_BUTTON_LABEL_PLAY_Y_PERCENT
BUTTON_LABEL_STOP_Y_PERCENT = DEFAULT_BUTTON_LABEL_STOP_Y_PERCENT
BUTTON_LABEL_NEXT_Y_PERCENT = DEFAULT_BUTTON_LABEL_NEXT_Y_PERCENT
POLL_SECONDS = DEFAULT_POLL_SECONDS
WEATHER_REFRESH_SECONDS = DEFAULT_WEATHER_REFRESH_SECONDS
PROGRESS_UPDATE_SECONDS = DEFAULT_PROGRESS_UPDATE_SECONDS
TIMELINE_POLL_MIN_INTERVAL_SECONDS = float(env("TIMELINE_POLL_MIN_INTERVAL_SECONDS", "8"))
LOW_POWER_COVER_RENDER = env("LOW_POWER_COVER_RENDER", "1").strip().lower() in TRUTHY_ENV_VALUES
DISPLAY_X_SHIFT = DEFAULT_DISPLAY_X_SHIFT
CONTROLLER_CLIENT_ID = env("CONTROLLER_CLIENT_ID", f"plexlcd-{socket.gethostname()}")
DEBUG_LOGGING = env("DEBUG_LOGGING", "0").strip().lower() in TRUTHY_ENV_VALUES
HTTP_TIMEOUT = DEFAULT_HTTP_TIMEOUT
COVER_RETRY_SECONDS = DEFAULT_COVER_RETRY_SECONDS
TOAST_DURATION_SECONDS = DEFAULT_TOAST_DURATION_SECONDS
NO_TRACK_GRACE_SECONDS = DEFAULT_NO_TRACK_GRACE_SECONDS
COMMAND_CONFIRM_SECONDS = DEFAULT_COMMAND_CONFIRM_SECONDS
BUTTON_DEVICES = []
RUNTIME_STATE = RuntimeState()
REFRESH_EVENT = threading.Event()
COMMAND_COUNTER_LOCK = threading.Lock()
DISPLAY_RETRY_SECONDS = 60


# Font lookup helpers
def first_existing_font(*candidates: str) -> str:
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


FONT_PATH_REGULAR = first_existing_font(
    env("FONT_PATH_REGULAR", ""),
    *DEFAULT_FONT_REGULAR_CANDIDATES,
)
FONT_PATH_BOLD = first_existing_font(
    env("FONT_PATH_BOLD", ""),
    *DEFAULT_FONT_BOLD_CANDIDATES,
)
FONT_PATH_SYMBOLS = first_existing_font(
    env("FONT_PATH_SYMBOLS", ""),
    *DEFAULT_FONT_SYMBOLS_CANDIDATES,
)


def log_message(component: str, message: str, *, level: str = "INFO", stderr: bool = False) -> None:
    """Small logging shim used across modules to keep output format consistent."""

    stream = sys.stderr if stderr else sys.stdout
    print(f"[{component}] [{level}] {message}", file=stream, flush=True)


def log_debug(component: str, message: str) -> None:
    if DEBUG_LOGGING:
        log_message(component, message, level="DEBUG", stderr=True)


def log_exception(component: str, context: str, exc: Exception, *, level: str = "ERROR") -> None:
    log_message(component, f"{context}: {exc}", level=level, stderr=True)


def display_is_available() -> bool:
    """Return True when framebuffer device appears to be present and writable."""

    if not os.path.exists(FB_DEVICE):
        return False
    try:
        with open(FB_DEVICE, "ab", buffering=0):
            return True
    except (PermissionError, OSError):
        return False


def wait_for_display() -> None:
    """Block until a framebuffer device is available, checking once per minute."""

    while not display_is_available():
        log_message(
            "startup",
            f"No display detected at {FB_DEVICE}; retrying in {DISPLAY_RETRY_SECONDS} seconds",
            level="WARN",
            stderr=True,
        )
        time.sleep(DISPLAY_RETRY_SECONDS)

    log_message("startup", f"Display detected at {FB_DEVICE}")


# State normalization and UI label helpers
def get_location_label() -> str:
    if LOCATION_NAME:
        return LOCATION_NAME
    if "/" in TIMEZONE:
        return TIMEZONE.split("/", 1)[1].replace("_", " ")
    return TIMEZONE.replace("_", " ")


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


FONT_TIME = load_font(FONT_PATH_BOLD, 54)
FONT_WEATHER = load_font(FONT_PATH_REGULAR, 24)
FONT_SMALL = load_font(FONT_PATH_REGULAR, 18)
FONT_TRACK = load_font(FONT_PATH_BOLD, 18)
FONT_META = load_font(FONT_PATH_REGULAR, 18)
FONT_LABEL = load_font(FONT_PATH_SYMBOLS, 12)
FONT_WEATHER_ICON = load_font(FONT_PATH_SYMBOLS, 22)
FONT_PROGRESS = load_font(FONT_PATH_REGULAR, 12)
FONT_TOAST = load_font(FONT_PATH_REGULAR, 14)


def format_ms(ms: Optional[int]) -> str:
    if ms is None or ms < 0:
        return "--:--"
    total_sec = ms // 1000
    return f"{total_sec // 60}:{total_sec % 60:02d}"


def toast_is_visible(now_ts: Optional[float] = None) -> bool:
    if now_ts is None:
        now_ts = time.monotonic()
    return bool(RUNTIME_STATE.toast_text and now_ts <= RUNTIME_STATE.toast_until_ts)


def draw_toast(img: Image.Image) -> Image.Image:
    if not toast_is_visible():
        return img
    text = RUNTIME_STATE.toast_text
    if not text:
        return img

    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    bbox = od.textbbox((0, 0), text, font=FONT_TOAST)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = 6
    pad_y = 3
    px = max(0, (WIDTH - (tw + pad_x * 2)) // 2)
    py = 3
    od.rounded_rectangle((px, py, px + tw + pad_x * 2, py + th + pad_y * 2), radius=5, fill=(0, 0, 0, 150))
    od.text((px + pad_x - bbox[0], py + pad_y - bbox[1] + 1), text, font=FONT_TOAST, fill="#ffffff")
    return Image.alpha_composite(base, overlay).convert("RGB")


# Startup validation and config application
def validate_startup():
    """Parse and validate configuration at startup, then apply runtime globals."""
    # Assumption: validation must fail-fast before any framebuffer/network side effects.
    cfg, errors = Config.from_env(button_available=Button is not None)
    if errors:
        log_message("startup", "Configuration errors:", level="ERROR", stderr=True)
        for err in errors:
            log_message("startup", f"- {err}", level="ERROR", stderr=True)
        sys.exit(1)

    _fields = (
        "PLEX_SERVER", "PLEX_TOKEN", "PLAYER_NAME", "LATITUDE", "LONGITUDE",
        "TIMEZONE", "LOCATION_NAME", "FB_DEVICE", "WIDTH", "HEIGHT", "BUTTONS_ENABLED",
        "BUTTON_PLAY_PAUSE_PIN", "BUTTON_STOP_PIN", "BUTTON_NEXT_PIN", "BUTTON_BOUNCE_TIME",
        "BUTTON_LABEL_PLAY_Y_PERCENT", "BUTTON_LABEL_STOP_Y_PERCENT", "BUTTON_LABEL_NEXT_Y_PERCENT",
        "POLL_SECONDS", "WEATHER_REFRESH_SECONDS", "PROGRESS_UPDATE_SECONDS",
        "NO_TRACK_GRACE_SECONDS", "DISPLAY_X_SHIFT", "DEBUG_LOGGING",
    )
    globals().update({f: getattr(cfg, f.lower()) for f in _fields})

    log_message("startup", "Configuration validated successfully")


def button_controller_config() -> ButtonControllerConfig:
    return ButtonControllerConfig(
        buttons_enabled=BUTTONS_ENABLED,
        play_pause_pin=BUTTON_PLAY_PAUSE_PIN,
        stop_pin=BUTTON_STOP_PIN,
        next_pin=BUTTON_NEXT_PIN,
        bounce_time=BUTTON_BOUNCE_TIME,
        plex_server=PLEX_SERVER,
        plex_token=PLEX_TOKEN,
        controller_client_id=CONTROLLER_CLIENT_ID,
        http_timeout=HTTP_TIMEOUT,
        toast_duration_seconds=TOAST_DURATION_SECONDS,
        no_track_grace_seconds=NO_TRACK_GRACE_SECONDS,
        command_confirm_seconds=COMMAND_CONFIRM_SECONDS,
    )


def send_plex_playback_command(action: str):
    """Send a playback command to the active Plexamp player."""
    dispatch_playback_command(
        action,
        config=button_controller_config(),
        runtime_state=RUNTIME_STATE,
        refresh_event=REFRESH_EVENT,
        command_counter_lock=COMMAND_COUNTER_LOCK,
        send_playback_request=send_playback_command,
        apply_button_rules=apply_button_rules,
        log_info=lambda msg: log_message("buttons", msg, level="INFO", stderr=True),
        log_warn=lambda msg: log_message("buttons", msg, level="WARN", stderr=True),
        log_debug=lambda msg: log_debug("buttons", msg),
        log_error=lambda msg: log_message("buttons", msg, level="ERROR", stderr=True),
    )


def setup_gpio_buttons():
    """Initialize GPIO button callbacks when hardware buttons are enabled."""
    if BUTTONS_ENABLED and Button is None:
        log_message(
            "buttons",
            "BUTTONS_ENABLED=1 but gpiozero is unavailable; continuing with buttons disabled",
            level="WARN",
            stderr=True,
        )
        return

    setup_button_devices(
        button_class=Button,
        button_devices=BUTTON_DEVICES,
        runtime_state=RUNTIME_STATE,
        config=button_controller_config(),
        dispatch_action=send_plex_playback_command,
        log_message=lambda msg: log_message("buttons", msg, level="INFO", stderr=True),
    )


def text_center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill="white"):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = max(0, (WIDTH - w) // 2)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_centered_row(draw: ImageDraw.ImageDraw, y: int, parts) -> None:
    """Draw a centered row composed of mixed text segments and fonts."""

    widths = [int(draw.textlength(text, font=font)) for text, font, _ in parts]
    x = max(0, (WIDTH - sum(widths)) // 2)
    for (text, font, fill), w in zip(parts, widths):
        draw.text((x, y), text, font=font, fill=fill)
        x += w


def draw_button_labels(
    img: Image.Image,
    fill: str = "#d8d8d8",
    is_playing: bool = False,
    visible_actions: Optional[tuple[str, ...]] = None,
) -> Image.Image:
    """Draw small button hint icons based on visible action set."""

    if not BUTTONS_ENABLED:
        return img

    button_items = [
        ("play_pause", "▌▌" if is_playing else "▶", HEIGHT * BUTTON_LABEL_PLAY_Y_PERCENT // 100),
        ("stop", "■", HEIGHT * BUTTON_LABEL_STOP_Y_PERCENT // 100),
        ("next", "⏭", HEIGHT * BUTTON_LABEL_NEXT_Y_PERCENT // 100),
    ]
    if visible_actions is not None:
        visible_set = set(visible_actions)
        button_items = [item for item in button_items if item[0] in visible_set]
    if not button_items:
        return img

    x = 6

    # Draw semi-transparent circle backgrounds on a composited overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    tmp = ImageDraw.Draw(img)
    radius = 10
    for _, label, label_y in button_items:
        bbox = tmp.textbbox((x, label_y), label, font=FONT_LABEL)
        cx = (bbox[0] + bbox[2]) // 2
        cy = (bbox[1] + bbox[3]) // 2
        od.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(0, 0, 0, 120))

    base = img.convert("RGBA")
    base = Image.alpha_composite(base, overlay)
    img = base.convert("RGB")

    draw = ImageDraw.Draw(img)
    for _, label, label_y in button_items:
        draw.text((x, label_y), label, font=FONT_LABEL, fill=fill)

    return img


def truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    t = text
    while t:
        t = t[:-1]
        candidate = t + ell
        if draw.textlength(candidate, font=font) <= max_width:
            return candidate
    return ell


def fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    method = Image.Resampling.BILINEAR if LOW_POWER_COVER_RENDER else Image.Resampling.LANCZOS
    return ImageOps.fit(img.convert("RGB"), (w, h), method=method)


# Rendering pipeline for idle and now-playing screens
def render_idle(
    weather: Optional[WeatherInfo],
    playback_status: Optional[str] = None,
    playback_state: Optional[str] = None,
) -> Image.Image:
    """Render idle screen with weather card and optional paused-status/footer controls."""

    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    now = datetime.now(ZoneInfo(TIMEZONE))
    text_center(draw, 14, now.strftime("%H:%M"), FONT_TIME, fill="white")
    text_center(draw, 74, now.strftime("%a %d %b"), FONT_SMALL, fill="#cfcfcf")
    text_center(draw, 96, get_location_label(), FONT_SMALL, fill="#9f9f9f")

    if weather:
        symbol = get_weather_symbol(weather.weather_code, weather.is_day)
        temp = f"{round(weather.temp_c):.0f}°C"
        label = WEATHER_CODES.get(weather.weather_code, f"Code {weather.weather_code}")
        text_center(draw, 122, symbol, FONT_WEATHER_ICON, fill="white")
        text_center(draw, 148, temp, FONT_WEATHER, fill="white")
        text_center(draw, 172, label, FONT_SMALL, fill="#cfcfcf")

        if weather.next_hour_weather_code is not None and weather.next_hour_temp_c is not None:
            _draw_centered_row(draw, 198, [
                ("Next hr:", FONT_PROGRESS, "#b8b8b8"),
                (get_weather_symbol(weather.next_hour_weather_code, weather.is_day), FONT_WEATHER_ICON, "#b8b8b8"),
                (f"{round(weather.next_hour_temp_c):.0f}C", FONT_SMALL, "#b8b8b8"),
            ])
    else:
        text_center(draw, 152, "Weather unavailable", FONT_SMALL, fill="#888888")

    if playback_status:
        text_center(draw, 2, playback_status, FONT_PROGRESS, fill="#9f9f9f")

    idle_actions: tuple[str, ...] = ()
    if playback_state == "paused":
        idle_actions = ("play_pause", "stop", "next")

    img = draw_button_labels(
        img,
        fill="#8f8f8f",
        is_playing=False,
        visible_actions=idle_actions,
    )
    return draw_toast(img)


def render_now_playing(cover: Image.Image, track: PlexTrack, elapsed_ms: Optional[int] = None) -> Image.Image:
    """Render now-playing screen with compact metadata and progress strip."""

    bg = fit_cover(cover, WIDTH, HEIGHT)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, HEIGHT - 90, WIDTH, HEIGHT), fill=(0, 0, 0, 150))
    composed = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(composed)

    text_x = 34
    text_max_width = WIDTH - text_x - 8
    title = truncate(draw, track.title, FONT_TRACK, text_max_width)
    artist = truncate(draw, track.artist, FONT_META, text_max_width)
    draw.text((text_x, HEIGHT - 86), title, font=FONT_TRACK, fill="white")
    draw.text((text_x, HEIGHT - 58), artist, font=FONT_META, fill="#dddddd")

    if track.duration_ms and track.duration_ms > 0:
        raw_elapsed = elapsed_ms if elapsed_ms is not None else (track.elapsed_ms or 0)
        elapsed = max(0, min(raw_elapsed, track.duration_ms))
        bar_x = text_x
        bar_y = HEIGHT - 12
        bar_w = WIDTH - text_x - 8
        bar_h = 2
        fill_w = int((elapsed / track.duration_ms) * bar_w)
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), fill="#4a4a4a")
        draw.rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), fill="#f2f2f2")
        elapsed_text = format_ms(elapsed)
        total_text = format_ms(track.duration_ms)
        time_y = bar_y - 17
        draw.text((bar_x, time_y), elapsed_text, font=FONT_PROGRESS, fill="#d6d6d6")
        total_w = int(draw.textlength(total_text, font=FONT_PROGRESS))
        draw.text((bar_x + bar_w - total_w, time_y), total_text, font=FONT_PROGRESS, fill="#d6d6d6")

    img = draw_button_labels(
        composed,
        is_playing=True,
        visible_actions=("play_pause", "stop", "next"),
    )
    return draw_toast(img)


# Main loop orchestration helpers
def refresh_weather_if_due(state: LoopState, now_ts: float) -> None:
    """Refresh cached weather on configured interval only."""

    if now_ts - state.last_weather_fetch > WEATHER_REFRESH_SECONDS:
        state.last_weather = fetch_weather(
            latitude=LATITUDE,
            longitude=LONGITUDE,
            timezone=TIMEZONE,
            timeout=HTTP_TIMEOUT,
            log_warn=lambda msg: log_message("weather", msg, level="WARN", stderr=True),
            log_error=lambda msg, exc: log_exception("weather", msg, exc),
        )
        state.last_weather_fetch = now_ts


def render_playing_frame(state: LoopState, track: PlexTrack, now_ts: float) -> None:
    """Render now-playing state, refreshing cover art only when needed."""

    display_config = DisplayAdapterConfig(FB_DEVICE, WIDTH, HEIGHT, DISPLAY_X_SHIFT)
    display_logger = DisplayAdapterLogger(
        log_exception=log_exception,
        log_message=lambda component, message: log_message(component, message, level="ERROR", stderr=True),
    )

    plan = resolve_playing_render_plan(
        state,
        track,
        now_ts,
        progress_update_seconds=PROGRESS_UPDATE_SECONDS,
        toast_visible=toast_is_visible(now_ts),
    )
    log_debug("loop", f"title={track.title!r} thumb={plan.thumb_changed} progress={plan.progress_changed} "
              f"toast={plan.toast_changed} refresh={plan.needs_refresh} retry={plan.needs_retry} "
              f"cover={state.cached_cover is not None}")

    if not plan.should_render:
        return

    if plan.thumb_changed:
        state.cached_cover = None

    if not state.cached_cover and track.thumb_path:
        state.cached_cover = fetch_cover(
            thumb_path=track.thumb_path,
            plex_server=PLEX_SERVER,
            plex_token=PLEX_TOKEN,
            width=WIDTH,
            height=HEIGHT,
            timeout=HTTP_TIMEOUT,
            log_warn=lambda msg: log_message("plex", msg, level="WARN", stderr=True),
            log_error=lambda msg: log_message("plex", msg, level="ERROR", stderr=True),
        )

    if state.cached_cover:
        if try_write_framebuffer(
            render_now_playing(state.cached_cover, track, elapsed_ms=plan.display_elapsed_ms),
            context="now-playing render",
            config=display_config,
            logger=display_logger,
        ):
            commit_playing_render_state(state, track, plan, next_retry_ts=0.0)
            return
        write_fallback_placeholder(
            "Render Error",
            context="now-playing render",
            font=FONT_SMALL,
            config=display_config,
            logger=display_logger,
        )
        return

    next_retry_ts = now_ts + COVER_RETRY_SECONDS
    if try_write_framebuffer(
        display_create_error_placeholder(WIDTH, HEIGHT, "No Album Art", FONT_SMALL),
        context="no-album-art render",
        config=display_config,
        logger=display_logger,
    ):
        commit_playing_render_state(state, track, plan, next_retry_ts=next_retry_ts)
    else:
        state.next_cover_retry_ts = next_retry_ts


def render_idle_frame(state: LoopState, track: Optional[PlexTrack]) -> None:
    """Render idle/paused/stopped screen when minute or state changes."""
    # Assumption: minute-level redraw cadence is sufficient for clock in idle mode.
    display_config = DisplayAdapterConfig(FB_DEVICE, WIDTH, HEIGHT, DISPLAY_X_SHIFT)
    display_logger = DisplayAdapterLogger(
        log_exception=log_exception,
        log_message=lambda component, message: log_message(component, message, level="ERROR", stderr=True),
    )
    minute_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    plan = resolve_idle_render_plan(
        state,
        minute_key,
        track,
        playback_status_text(track.state) if track and track.state == "paused" else None,
        toast_is_visible(),
    )

    if not plan.should_render:
        return

    if try_write_framebuffer(
        render_idle(state.last_weather, playback_status=plan.status_text, playback_state=plan.idle_state),
        context="idle render",
        config=display_config,
        logger=display_logger,
    ):
        commit_idle_render_state(state, plan)
        return

    write_fallback_placeholder(
        "Display Error",
        context="idle render",
        font=FONT_SMALL,
        config=display_config,
        logger=display_logger,
    )


def wait_for_next_cycle(state: LoopState) -> None:
    """Wait for poll interval or immediate wake-up triggered by button commands."""
    # Assumption: small post-command delay gives Plex state time to settle before next poll.
    remaining = None
    if toast_is_visible():
        remaining = max(0.0, RUNTIME_STATE.toast_until_ts - time.monotonic())
    timeout = resolve_wait_timeout(
        last_player_state=state.last_player_state,
        poll_seconds=POLL_SECONDS,
        progress_update_seconds=PROGRESS_UPDATE_SECONDS,
        toast_remaining_seconds=remaining,
    )
    REFRESH_EVENT.wait(timeout=timeout)
    if REFRESH_EVENT.is_set():
        REFRESH_EVENT.clear()
        time.sleep(0.5)


def render_from_transition(state: LoopState, decision: TransitionMode, track: Optional[PlexTrack], now_ts: float, idle_track: Optional[PlexTrack]) -> None:
    """Route rendering based on resolved transition mode."""

    if decision == TransitionMode.PLAYING and track is not None:
        render_playing_frame(state, track, now_ts)
        return
    if decision == TransitionMode.HOLD:
        # Preserve current now-playing frame while Plex transitions between tracks.
        return
    render_idle_frame(state, idle_track)


# Application entrypoint
def main():
    """Main app loop: fetch state, render frame, and sleep/wake for next cycle."""
    # Assumption: top-level loop must be resilient; all recoverable errors are logged and retried.
    validate_startup()
    wait_for_display()
    setup_gpio_buttons()

    state = LoopState()
    collector_config = PlaybackCollectorConfig(PLAYER_NAME, PLEX_SERVER, PLEX_TOKEN, HTTP_TIMEOUT)
    collector_deps = PlaybackCollectorDeps(
        fetch_sessions_json=fetch_sessions_json,
        find_player_track=find_player_track,
        fetch_player_timeline_state=fetch_player_timeline_state,
        should_poll_timeline=should_poll_timeline,
        log_warn=lambda msg: log_message("plex", msg, level="WARN", stderr=True),
        log_error=lambda msg: log_message("plex", msg, level="ERROR", stderr=True),
    )
    last_timeline_poll_ts = 0.0

    while True:
        try:
            now_ts = time.monotonic()
            refresh_weather_if_due(state, now_ts)

            timeline_interval = max(1.0, float(TIMELINE_POLL_MIN_INTERVAL_SECONDS))
            timeline_due = (now_ts - last_timeline_poll_ts) >= timeline_interval

            collected = collect_playback_snapshot(
                now_ts=now_ts,
                loop_state=state,
                runtime_state=RUNTIME_STATE,
                config=collector_config,
                deps=collector_deps,
                enable_timeline_poll=timeline_due,
                last_timeline_poll_ts=last_timeline_poll_ts,
                timeline_poll_min_interval_seconds=timeline_interval,
            )
            if collected.snapshot.timeline_state is not None:
                last_timeline_poll_ts = now_ts
            decision = resolve_transition(collected.snapshot, no_track_grace_seconds=NO_TRACK_GRACE_SECONDS)
            apply_transition_decision(RUNTIME_STATE, state, decision)

            render_from_transition(state, decision.mode, collected.track, now_ts, decision.idle_track)

        except KeyboardInterrupt:
            raise
        except PermissionError as exc:
            log_exception("main", "Permission error (framebuffer not writable?)", exc)
            time.sleep(5)
        except Exception as exc:
            log_exception("main", "Unhandled loop error", exc)
            time.sleep(5)

        wait_for_next_cycle(state)


if __name__ == "__main__":
    main()
