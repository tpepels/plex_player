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
from config import Config
from models import LoopState, PlaybackSnapshot, PlexTrack, RuntimeState, TransitionMode, WeatherInfo
from plex_service import (
    fetch_cover,
    fetch_player_timeline_state,
    fetch_sessions_json,
    find_player_track,
    playback_status_text,
    send_playback_command,
)
from transition_rules import apply_button_rules, compute_display_elapsed_ms, resolve_transition
from weather_service import WEATHER_CODES, fetch_weather, get_weather_symbol
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
PLEX_SERVER = env("PLEX_SERVER", "http://plex.local:32400").rstrip("/")
PLEX_TOKEN = env("PLEX_TOKEN", "")
PLAYER_NAME = env("PLAYER_NAME", "Plexamp Pi Zero")
LATITUDE = 0.0
LONGITUDE = 0.0
TIMEZONE = env("TIMEZONE", "UTC")
LOCATION_NAME = env("LOCATION_NAME", "").strip()
FB_DEVICE = env("FB_DEVICE", "/dev/fb1")
WIDTH = 320
HEIGHT = 240
BUTTONS_ENABLED = False
BUTTON_PLAY_PAUSE_PIN = 23
BUTTON_STOP_PIN = 24
BUTTON_NEXT_PIN = 25
BUTTON_BOUNCE_TIME = 0.15
BUTTON_LABEL_PLAY_Y_PERCENT = 20
BUTTON_LABEL_STOP_Y_PERCENT = 40
BUTTON_LABEL_NEXT_Y_PERCENT = 60
POLL_SECONDS = 3
WEATHER_REFRESH_SECONDS = 900
PROGRESS_UPDATE_SECONDS = 3
DISPLAY_X_SHIFT = 0
CONTROLLER_CLIENT_ID = env("CONTROLLER_CLIENT_ID", f"plexlcd-{socket.gethostname()}")
DEBUG_LOGGING = env("DEBUG_LOGGING", "0").strip().lower() in {"1", "true", "yes", "on"}
HTTP_TIMEOUT = 10
COVER_RETRY_SECONDS = 20
TOAST_DURATION_SECONDS = 0.7
NO_TRACK_GRACE_SECONDS = 4.0
COMMAND_CONFIRM_SECONDS = 5.0
BUTTON_DEVICES = []
RUNTIME_STATE = RuntimeState()
REFRESH_EVENT = threading.Event()


# Font lookup helpers
def first_existing_font(*candidates: str) -> str:
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


FONT_PATH_REGULAR = first_existing_font(
    env("FONT_PATH_REGULAR", ""),
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
FONT_PATH_BOLD = first_existing_font(
    env("FONT_PATH_BOLD", ""),
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
FONT_PATH_SYMBOLS = first_existing_font(
    env("FONT_PATH_SYMBOLS", ""),
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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

    globals().update(
        {
            "PLEX_SERVER": cfg.plex_server,
            "PLEX_TOKEN": cfg.plex_token,
            "PLAYER_NAME": cfg.player_name,
            "LATITUDE": cfg.latitude,
            "LONGITUDE": cfg.longitude,
            "TIMEZONE": cfg.timezone,
            "LOCATION_NAME": cfg.location_name,
            "FB_DEVICE": cfg.fb_device,
            "WIDTH": cfg.width,
            "HEIGHT": cfg.height,
            "BUTTONS_ENABLED": cfg.buttons_enabled,
            "BUTTON_PLAY_PAUSE_PIN": cfg.button_play_pause_pin,
            "BUTTON_STOP_PIN": cfg.button_stop_pin,
            "BUTTON_NEXT_PIN": cfg.button_next_pin,
            "BUTTON_BOUNCE_TIME": cfg.button_bounce_time,
            "BUTTON_LABEL_PLAY_Y_PERCENT": cfg.button_label_play_y_percent,
            "BUTTON_LABEL_STOP_Y_PERCENT": cfg.button_label_stop_y_percent,
            "BUTTON_LABEL_NEXT_Y_PERCENT": cfg.button_label_next_y_percent,
            "POLL_SECONDS": cfg.poll_seconds,
            "WEATHER_REFRESH_SECONDS": cfg.weather_refresh_seconds,
            "PROGRESS_UPDATE_SECONDS": cfg.progress_update_seconds,
            "NO_TRACK_GRACE_SECONDS": cfg.no_track_grace_seconds,
            "DISPLAY_X_SHIFT": cfg.display_x_shift,
            "DEBUG_LOGGING": cfg.debug_logging,
        }
    )

    log_message("startup", "Configuration validated successfully")


def next_command_id() -> int:
    """Return monotonically increasing command id for Plex playback endpoints."""
    RUNTIME_STATE.command_counter += 1
    return RUNTIME_STATE.command_counter


def send_plex_playback_command(action: str):
    """Send a playback command to the active Plexamp player."""
    # Assumption: toasts reflect the intended action immediately after accepted request.
    cmd_id = next_command_id()
    sent_ok = send_playback_command(
        action=action,
        plex_server=PLEX_SERVER,
        plex_token=PLEX_TOKEN,
        controller_client_id=CONTROLLER_CLIENT_ID,
        target_client_id=RUNTIME_STATE.current_target_client_id,
        player_addr=RUNTIME_STATE.current_player_address,
        player_port=RUNTIME_STATE.current_player_port,
        command_id=cmd_id,
        timeout=HTTP_TIMEOUT,
        log_info=lambda msg: log_message("buttons", msg, level="INFO", stderr=True),
        log_warn=lambda msg: log_message("buttons", msg, level="WARN", stderr=True),
        log_debug=lambda msg: log_debug("buttons", msg),
        log_error=lambda msg: log_message("buttons", msg, level="ERROR", stderr=True),
    )
    if sent_ok:
        apply_button_rules(
            RUNTIME_STATE,
            action,
            command_id=cmd_id,
            now_ts=time.monotonic(),
            toast_duration_seconds=TOAST_DURATION_SECONDS,
            stop_force_idle_seconds=max(2.0, NO_TRACK_GRACE_SECONDS),
            confirm_timeout_seconds=COMMAND_CONFIRM_SECONDS,
        )
        REFRESH_EVENT.set()


def setup_gpio_buttons():
    """Initialize GPIO button callbacks when hardware buttons are enabled."""
    # Assumption: gpiozero handles debouncing; callback should stay lightweight.
    global BUTTON_DEVICES
    if not BUTTONS_ENABLED:
        return
    if Button is None:
        return

    buttons = [
        ("play_pause", BUTTON_PLAY_PAUSE_PIN),
        ("stop", BUTTON_STOP_PIN),
        ("next", BUTTON_NEXT_PIN),
    ]

    for action, pin in buttons:
        button = Button(pin, pull_up=True, bounce_time=BUTTON_BOUNCE_TIME)
        def _make_handler(action_name, pin_no):
            def handler():
                print(f"[buttons] GPIO pin {pin_no} pressed → action={action_name}  client_id={RUNTIME_STATE.current_target_client_id!r}", file=sys.stderr, flush=True)
                send_plex_playback_command(action_name)
            return handler
        button.when_pressed = _make_handler(action, pin)
        BUTTON_DEVICES.append(button)

    log_message(
        "buttons",
        f"Enabled GPIO buttons: play/pause={BUTTON_PLAY_PAUSE_PIN}, stop={BUTTON_STOP_PIN}, next={BUTTON_NEXT_PIN}",
    )


def create_error_placeholder(text: str = "Display Error") -> Image.Image:
    """Create a fallback image when cover/rendering fails."""
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    text_center(draw, (HEIGHT - 20) // 2, text, FONT_SMALL, fill="#ff6666")
    return img


def text_center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill="white"):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = max(0, (WIDTH - w) // 2)
    draw.text((x, y), text, font=font, fill=fill)


def draw_button_labels(
    img: Image.Image,
    y: int,
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
    return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)


def apply_display_shift(img: Image.Image) -> Image.Image:
    """Apply small horizontal correction for framebuffer driver panel offsets."""
    if DISPLAY_X_SHIFT == 0:
        return img
    shifted = Image.new("RGB", (WIDTH, HEIGHT), "black")
    shifted.paste(img, (DISPLAY_X_SHIFT, 0))
    return shifted


def rgb888_to_rgb565_bytes(img: Image.Image) -> bytes:
    """Convert RGB888 PIL image to little-endian RGB565 byte buffer for framebuffer."""

    if img.mode != "RGB":
        img = img.convert("RGB")
    pixels = img.load()
    if pixels is None:
        raise ValueError("Failed to access pixel buffer")
    out = bytearray(WIDTH * HEIGHT * 2)
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            px = pixels[x, y]
            if not isinstance(px, tuple) or len(px) < 3:
                raise ValueError("Unexpected pixel format in RGB buffer")
            r, g, b = int(px[0]), int(px[1]), int(px[2])
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[i] = value & 0xFF
            out[i + 1] = (value >> 8) & 0xFF
            i += 2
    return bytes(out)


def write_framebuffer(img: Image.Image):
    """Write final rendered frame to framebuffer device."""

    try:
        raw = rgb888_to_rgb565_bytes(apply_display_shift(img))
        with open(FB_DEVICE, "wb", buffering=0) as fb:
            fb.write(raw)
    except PermissionError:
        print(f"[framebuffer] Permission denied writing to {FB_DEVICE}. Need root or video group membership.", file=sys.stderr)
        raise
    except IOError as e:
        print(f"[framebuffer] I/O error: {e}", file=sys.stderr)
        raise


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
            next_symbol = get_weather_symbol(weather.next_hour_weather_code, weather.is_day)
            next_temp_text = f"{round(weather.next_hour_temp_c):.0f}C"
            next_prefix = "Next hr:"
            prefix_bbox = draw.textbbox((0, 0), next_prefix, font=FONT_PROGRESS)
            icon_bbox = draw.textbbox((0, 0), next_symbol, font=FONT_WEATHER_ICON)
            temp_bbox = draw.textbbox((0, 0), next_temp_text, font=FONT_SMALL)
            prefix_w = prefix_bbox[2] - prefix_bbox[0]
            icon_w = icon_bbox[2] - icon_bbox[0]
            temp_w = temp_bbox[2] - temp_bbox[0]
            prefix_h = prefix_bbox[3] - prefix_bbox[1]
            icon_h = icon_bbox[3] - icon_bbox[1]
            temp_h = temp_bbox[3] - temp_bbox[1]
            gap = 6
            row_w = prefix_w + gap + icon_w + gap + temp_w
            row_x = max(0, (WIDTH - row_w) // 2)
            row_y = 198
            row_h = max(prefix_h, icon_h, temp_h)
            prefix_y = row_y + (row_h - prefix_h) // 2 - prefix_bbox[1]
            icon_y = row_y + (row_h - icon_h) // 2 - icon_bbox[1]
            temp_y = row_y + (row_h - temp_h) // 2 - temp_bbox[1]
            draw.text((row_x - prefix_bbox[0], prefix_y), next_prefix, font=FONT_PROGRESS, fill="#b8b8b8")
            draw.text((row_x + prefix_w + gap - icon_bbox[0], icon_y), next_symbol, font=FONT_WEATHER_ICON, fill="#b8b8b8")
            draw.text((row_x + prefix_w + gap + icon_w + gap - temp_bbox[0], temp_y), next_temp_text, font=FONT_SMALL, fill="#b8b8b8")
    else:
        text_center(draw, 152, "Weather unavailable", FONT_SMALL, fill="#888888")

    if playback_status:
        text_center(draw, 2, playback_status, FONT_PROGRESS, fill="#9f9f9f")

    idle_actions: tuple[str, ...] = ()
    if playback_state == "paused":
        idle_actions = ("play_pause", "stop", "next")

    img = draw_button_labels(
        img,
        0,
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
        time_y = bar_y - 16
        draw.text((bar_x, time_y), elapsed_text, font=FONT_PROGRESS, fill="#d6d6d6")
        total_w = int(draw.textlength(total_text, font=FONT_PROGRESS))
        draw.text((bar_x + bar_w - total_w, time_y), total_text, font=FONT_PROGRESS, fill="#d6d6d6")

    img = draw_button_labels(
        composed,
        0,
        is_playing=True,
        visible_actions=("play_pause", "stop", "next"),
    )
    return draw_toast(img)


def try_write_framebuffer(img: Image.Image, *, context: str) -> bool:
    """Write image to framebuffer and report errors consistently."""
    try:
        write_framebuffer(img)
        return True
    except Exception as exc:
        log_exception("framebuffer", f"Failed during {context}", exc)
        return False


def write_fallback_placeholder(text: str, *, context: str) -> None:
    """Best-effort write of a fallback error image."""
    img = create_error_placeholder(text)
    if not try_write_framebuffer(img, context=f"{context} placeholder"):
        log_message("framebuffer", f"Unable to display fallback placeholder for: {context}", level="ERROR", stderr=True)


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


def update_current_player_context(track: Optional[PlexTrack]) -> None:
    """Update globals used by button handlers from latest track context."""
    if track:
        RUNTIME_STATE.current_target_client_id = track.target_client_identifier
        RUNTIME_STATE.current_player_address = track.player_address
        RUNTIME_STATE.current_player_port = track.player_port
        RUNTIME_STATE.current_playback_state = track.state
    else:
        # Preserve last known player endpoint for direct timeline polling.
        RUNTIME_STATE.current_playback_state = "unknown"


def render_playing_frame(state: LoopState, track: PlexTrack, now_ts: float) -> None:
    """Render now-playing state, refreshing cover art only when needed."""
    # Assumption: cover art is expensive enough to cache between loop cycles.
    needs_refresh = (
        track.thumb_path != state.last_thumb_path
        or track.title != state.last_track_title
        or state.last_player_state != "playing"
    )
    display_elapsed_ms = compute_display_elapsed_ms(state, track, now_ts)
    elapsed_second = (display_elapsed_ms // 1000) if display_elapsed_ms is not None else None
    progress_bucket = (
        elapsed_second // PROGRESS_UPDATE_SECONDS
        if elapsed_second is not None and PROGRESS_UPDATE_SECONDS > 0
        else None
    )
    progress_changed = progress_bucket != state.last_elapsed_second
    toast_visible = toast_is_visible(now_ts)
    toast_visibility_changed = toast_visible != state.last_toast_visible
    needs_retry = (
        not state.cached_cover
        and track.thumb_path == state.last_thumb_path
        and now_ts >= state.next_cover_retry_ts
    )

    log_debug(
        "loop",
        (
            f"title={track.title!r} last_title={state.last_track_title!r} "
            f"thumb_changed={track.thumb_path != state.last_thumb_path} "
            f"progress_changed={progress_changed} "
            f"toast_changed={toast_visibility_changed} "
            f"needs_refresh={needs_refresh} needs_retry={needs_retry} "
            f"have_cover={state.cached_cover is not None}"
        ),
    )

    if not (needs_refresh or needs_retry or progress_changed or toast_visibility_changed):
        return

    if track.thumb_path != state.last_thumb_path:
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
            render_now_playing(state.cached_cover, track, elapsed_ms=display_elapsed_ms),
            context="now-playing render",
        ):
            state.last_thumb_path = track.thumb_path
            state.last_track_title = track.title
            state.last_player_state = "playing"
            state.last_elapsed_second = progress_bucket
            state.last_toast_visible = toast_visible
            state.next_cover_retry_ts = 0.0
            return
        write_fallback_placeholder("Render Error", context="now-playing render")
        return

    state.next_cover_retry_ts = now_ts + COVER_RETRY_SECONDS
    if try_write_framebuffer(create_error_placeholder("No Album Art"), context="no-album-art render"):
        state.last_thumb_path = track.thumb_path
        state.last_track_title = track.title
        state.last_player_state = "playing"
        state.last_elapsed_second = progress_bucket
        state.last_toast_visible = toast_visible


def render_idle_frame(state: LoopState, track: Optional[PlexTrack]) -> None:
    """Render idle/paused/stopped screen when minute or state changes."""
    # Assumption: minute-level redraw cadence is sufficient for clock in idle mode.
    minute_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    idle_state = track.state if track else "unknown"
    status_text = playback_status_text(idle_state) if idle_state == "paused" else None
    toast_visible = toast_is_visible()

    if minute_key == state.last_idle_minute and state.last_player_state == idle_state and toast_visible == state.last_toast_visible:
        return

    if try_write_framebuffer(
        render_idle(state.last_weather, playback_status=status_text, playback_state=idle_state),
        context="idle render",
    ):
        state.last_idle_minute = minute_key
        state.last_player_state = idle_state
        state.last_thumb_path = None
        state.last_track_title = None
        state.last_track_identity = None
        state.last_elapsed_second = None
        state.last_reported_elapsed_ms = None
        state.elapsed_anchor_ms = None
        state.elapsed_anchor_ts = 0.0
        state.last_toast_visible = toast_visible
        state.cached_cover = None
        state.next_cover_retry_ts = 0.0
        return

    write_fallback_placeholder("Display Error", context="idle render")


def wait_for_next_cycle(state: LoopState) -> None:
    """Wait for poll interval or immediate wake-up triggered by button commands."""
    # Assumption: small post-command delay gives Plex state time to settle before next poll.
    timeout = POLL_SECONDS
    if state.last_player_state == "playing":
        timeout = min(timeout, max(1, PROGRESS_UPDATE_SECONDS))
    if toast_is_visible():
        remaining = max(0.0, RUNTIME_STATE.toast_until_ts - time.monotonic())
        timeout = min(timeout, remaining)
    REFRESH_EVENT.wait(timeout=timeout)
    if REFRESH_EVENT.is_set():
        REFRESH_EVENT.clear()
        time.sleep(0.5)


# Application entrypoint
def main():
    """Main app loop: fetch state, render frame, and sleep/wake for next cycle."""
    # Assumption: top-level loop must be resilient; all recoverable errors are logged and retried.
    validate_startup()
    setup_gpio_buttons()

    state = LoopState()

    while True:
        try:
            now_ts = time.monotonic()
            refresh_weather_if_due(state, now_ts)

            sessions = fetch_sessions_json(
                plex_server=PLEX_SERVER,
                plex_token=PLEX_TOKEN,
                timeout=HTTP_TIMEOUT,
                log_warn=lambda msg: log_message("plex", msg, level="WARN", stderr=True),
                log_error=lambda msg: log_message("plex", msg, level="ERROR", stderr=True),
            )
            track = find_player_track(sessions, PLAYER_NAME) if sessions else None
            update_current_player_context(track)
            timeline_state: Optional[str] = None

            # Collect direct Plexamp timeline state as additional rule input.
            if (
                (track and track.state == "playing")
                or (not track and str(state.last_player_state or "").strip().lower() == "playing")
            ):
                timeline = fetch_player_timeline_state(
                    player_addr=RUNTIME_STATE.current_player_address,
                    player_port=RUNTIME_STATE.current_player_port,
                    plex_token=PLEX_TOKEN,
                    timeout=HTTP_TIMEOUT,
                    log_warn=lambda msg: log_message("plex", msg, level="WARN", stderr=True),
                )
                if timeline:
                    timeline_state = str(timeline.get("state") or "").strip().lower() or None

            snapshot = PlaybackSnapshot(
                now_ts=now_ts,
                track=track,
                last_player_state=state.last_player_state,
                no_track_grace_until_ts=state.no_track_grace_until_ts,
                force_idle_until_ts=RUNTIME_STATE.force_idle_until_ts,
                pending_command=RUNTIME_STATE.pending_command,
                timeline_state=timeline_state,
            )
            decision = resolve_transition(snapshot, no_track_grace_seconds=NO_TRACK_GRACE_SECONDS)

            if decision.clear_force_idle:
                RUNTIME_STATE.force_idle_until_ts = 0.0
            if decision.clear_pending_command:
                RUNTIME_STATE.pending_command = None
            if decision.set_no_track_grace_until_ts is not None:
                state.no_track_grace_until_ts = decision.set_no_track_grace_until_ts

            if decision.mode == TransitionMode.PLAYING and track is not None:
                render_playing_frame(state, track, now_ts)
            elif decision.mode == TransitionMode.HOLD:
                # Preserve current now-playing frame while Plex transitions between tracks.
                pass
            else:
                render_idle_frame(state, decision.idle_track)

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