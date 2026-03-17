#!/usr/bin/env python3
import io
import os
import socket
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from config import Config
from models import LoopState, PlexTrack, WeatherInfo
from zoneinfo import ZoneInfo

try:
    from gpiozero import Button
except Exception:
    Button = None


# Environment bootstrap helpers
def load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env file into process environment."""
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
DISPLAY_X_SHIFT = 0
CONTROLLER_CLIENT_ID = env("CONTROLLER_CLIENT_ID", f"plexlcd-{socket.gethostname()}")
DEBUG_LOGGING = env("DEBUG_LOGGING", "0").strip().lower() in {"1", "true", "yes", "on"}
HTTP_TIMEOUT = 10
COVER_RETRY_SECONDS = 20
BUTTON_DEVICES = []
CURRENT_TARGET_CLIENT_ID: Optional[str] = None
CURRENT_PLAYER_ADDRESS: Optional[str] = None
CURRENT_PLAYER_PORT: int = 32500
COMMAND_COUNTER = 1
REFRESH_EVENT = threading.Event()


# Font and weather display lookup tables
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


WEATHER_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Violent showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "T-storm + hail",
    99: "Heavy hail",
}

WEATHER_SYMBOLS = {
    0: ("☀", "☾"),
    1: ("🌤", "☾"),
    2: ("⛅", "☁"),
    3: ("☁", "☁"),
    45: ("🌫", "🌫"),
    48: ("🌫", "🌫"),
    51: ("🌦", "🌧"),
    53: ("🌦", "🌧"),
    55: ("🌧", "🌧"),
    61: ("🌦", "🌧"),
    63: ("🌧", "🌧"),
    65: ("🌧", "🌧"),
    71: ("🌨", "🌨"),
    73: ("🌨", "🌨"),
    75: ("❄", "❄"),
    77: ("❄", "❄"),
    80: ("🌦", "🌧"),
    81: ("🌧", "🌧"),
    82: ("⛈", "⛈"),
    85: ("🌨", "🌨"),
    86: ("❄", "❄"),
    95: ("⛈", "⛈"),
    96: ("⛈", "⛈"),
    99: ("⛈", "⛈"),
}


def log_message(component: str, message: str, *, level: str = "INFO", stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    print(f"[{component}] [{level}] {message}", file=stream, flush=True)


def log_debug(component: str, message: str) -> None:
    if DEBUG_LOGGING:
        log_message(component, message, level="DEBUG", stderr=True)


def log_exception(component: str, context: str, exc: Exception, *, level: str = "ERROR") -> None:
    log_message(component, f"{context}: {exc}", level=level, stderr=True)


# State normalization and UI label helpers
def normalize_playback_state(item: dict) -> str:
    """Normalize Plex session/player state with enough detail for UI text."""
    player = item.get("Player", {})
    session = item.get("Session", {})

    raw_states = [
        str(player.get("state") or "").strip().lower(),
        str(session.get("state") or "").strip().lower(),
    ]

    if "playing" in raw_states:
        return "playing"
    for state in ("paused", "stopped", "buffering", "idle"):
        if state in raw_states:
            return state
    for state in raw_states:
        if state and state != "none":
            return state
    return "unknown"


def playback_status_text(state: str) -> str:
    mapping = {
        "playing": "Playing",
        "paused": "Paused",
        "stopped": "Stopped",
        "buffering": "Buffering",
        "idle": "Stopped",
        "unknown": "Stopped",
    }
    return mapping.get(state, state.capitalize() if state else "Stopped")


def get_weather_symbol(weather_code: int, is_day: int) -> str:
    day_symbol, night_symbol = WEATHER_SYMBOLS.get(weather_code, ("?", "?"))
    return day_symbol if is_day else night_symbol


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


# Startup validation and config application
def validate_startup():
    """Parse and validate configuration at startup, then apply runtime globals."""
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
            "DISPLAY_X_SHIFT": cfg.display_x_shift,
            "DEBUG_LOGGING": cfg.debug_logging,
        }
    )

    log_message("startup", "Configuration validated successfully")


def next_command_id() -> int:
    global COMMAND_COUNTER
    COMMAND_COUNTER += 1
    return COMMAND_COUNTER


def send_plex_playback_command(action: str):
    """Send a playback command to the active Plexamp player."""
    target_client_id = CURRENT_TARGET_CLIENT_ID
    player_addr = CURRENT_PLAYER_ADDRESS
    player_port = CURRENT_PLAYER_PORT

    if not target_client_id:
        log_message("buttons", f"Ignoring {action}: no active Plex target client", level="WARN", stderr=True)
        return

    endpoint_map = {
        "play_pause": "playPause",
        "stop": "stop",
        "next": "skipNext",
    }
    endpoint = endpoint_map.get(action)
    if not endpoint:
        log_message("buttons", f"Ignoring unknown action: {action}", level="WARN", stderr=True)
        return

    # Send directly to the Plexamp player, not via the server
    if player_addr:
        base_url = f"http://{player_addr}:{player_port}"
    else:
        base_url = PLEX_SERVER
        log_message("buttons", "No player address known, falling back to server URL", level="WARN", stderr=True)

    url = f"{base_url}/player/playback/{endpoint}"
    cmd_id = next_command_id()
    log_message("buttons", f"{action} -> GET {url} commandID={cmd_id}", level="INFO", stderr=True)
    try:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "X-Plex-Token": PLEX_TOKEN,
                "X-Plex-Client-Identifier": CONTROLLER_CLIENT_ID,
            },
            params={
                "type": "music",
                "commandID": cmd_id,
            },
            timeout=HTTP_TIMEOUT,
        )
        log_debug("buttons", f"Response {resp.status_code}: {resp.text[:200]!r}")
        resp.raise_for_status()
        log_message("buttons", f"Sent {action} OK", level="INFO", stderr=True)
        REFRESH_EVENT.set()
    except Exception as exc:
        log_exception("buttons", f"Failed to send {action}", exc)


def setup_gpio_buttons():
    """Initialize GPIO button callbacks when hardware buttons are enabled."""
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
                print(f"[buttons] GPIO pin {pin_no} pressed → action={action_name}  client_id={CURRENT_TARGET_CLIENT_ID!r}", file=sys.stderr, flush=True)
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


def draw_button_labels(img: Image.Image, y: int, fill: str = "#d8d8d8", is_playing: bool = False) -> Image.Image:
    if not BUTTONS_ENABLED:
        return img

    labels = ["▌▌" if is_playing else "▶", "■", "⏭"]
    x = 6
    y_positions = [
        HEIGHT * BUTTON_LABEL_PLAY_Y_PERCENT // 100,
        HEIGHT * BUTTON_LABEL_STOP_Y_PERCENT // 100,
        HEIGHT * BUTTON_LABEL_NEXT_Y_PERCENT // 100,
    ]

    # Draw semi-transparent circle backgrounds on a composited overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    tmp = ImageDraw.Draw(img)
    radius = 10
    for label, label_y in zip(labels, y_positions):
        bbox = tmp.textbbox((x, label_y), label, font=FONT_LABEL)
        cx = (bbox[0] + bbox[2]) // 2
        cy = (bbox[1] + bbox[3]) // 2
        od.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(0, 0, 0, 120))

    base = img.convert("RGBA")
    base = Image.alpha_composite(base, overlay)
    img = base.convert("RGB")

    draw = ImageDraw.Draw(img)
    for label, label_y in zip(labels, y_positions):
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


# External I/O: weather + Plex API calls
def fetch_weather() -> Optional[WeatherInfo]:
    """Fetch weather with retry logic. Returns None on failure (uses cached value)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        "&current=temperature_2m,weather_code,is_day"
        f"&timezone={TIMEZONE}"
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            cur = r.json()["current"]
            return WeatherInfo(
                temp_c=float(cur["temperature_2m"]),
                weather_code=int(cur["weather_code"]),
                is_day=int(cur["is_day"]),
            )
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_message(
                    "weather",
                    f"Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...",
                    level="WARN",
                    stderr=True,
                )
                time.sleep(backoff)
            else:
                log_exception("weather", f"Failed after {max_retries} attempts", exc)
    return None


def fetch_sessions_json() -> Optional[dict]:
    """Fetch Plex sessions JSON with short retry/backoff."""
    if not PLEX_TOKEN:
        log_message("plex", "Missing PLEX_TOKEN", level="ERROR", stderr=True)
        return None
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            r = requests.get(
                f"{PLEX_SERVER}/status/sessions",
                params={"X-Plex-Token": PLEX_TOKEN},
                headers={"Accept": "application/json"},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_message(
                    "plex",
                    f"Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...",
                    level="WARN",
                    stderr=True,
                )
                time.sleep(backoff)
            else:
                log_exception("plex", f"Sessions failed after {max_retries} attempts", exc)
    return None


def find_player_track(data: dict) -> Optional[PlexTrack]:
    """Find the active track entry matching configured PLAYER_NAME."""
    container = data.get("MediaContainer", {})
    metadata = container.get("Metadata", [])
    if isinstance(metadata, dict):
        metadata = [metadata]

    for item in metadata:
        player = item.get("Player", {})
        title = (player.get("title") or player.get("name") or "").strip()
        if title == PLAYER_NAME:
            thumb = (
                item.get("thumb")
                or item.get("grandparentThumb")
                or item.get("parentThumb")
                or item.get("art")
            )
            return PlexTrack(
                title=item.get("title", "Unknown Track"),
                artist=item.get("grandparentTitle", "Unknown Artist"),
                album=item.get("parentTitle", "Unknown Album"),
                thumb_path=thumb,
                state=normalize_playback_state(item),
                target_client_identifier=(
                    player.get("machineIdentifier")
                    or player.get("clientIdentifier")
                    or item.get("machineIdentifier")
                ),
                player_address=player.get("address"),
                player_port=int(player.get("port") or 32500),
            )
    return None


def fetch_plex_cover(thumb_path: str) -> Optional[Image.Image]:
    """Fetch and scale cover art image for the active track."""
    if not thumb_path:
        return None

    max_retries = 2
    for attempt in range(max_retries):
        try:
            direct_url = thumb_path if thumb_path.startswith(("http://", "https://")) else f"{PLEX_SERVER}{thumb_path}"
            params = None
            if not thumb_path.startswith(("http://", "https://")):
                # Local Plex paths usually require token as query parameter.
                params = {"X-Plex-Token": PLEX_TOKEN}

            r = requests.get(
                direct_url,
                params=params,
                headers={"Accept": "image/*", "X-Plex-Token": PLEX_TOKEN},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.size != (WIDTH, HEIGHT):
                img = fit_cover(img, WIDTH, HEIGHT)
            return img
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_message(
                    "plex",
                    f"Cover attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...",
                    level="WARN",
                    stderr=True,
                )
                time.sleep(backoff)
            else:
                log_exception("plex", f"Cover failed after {max_retries} attempts", exc)
    return None


# Rendering pipeline for idle and now-playing screens
def render_idle(weather: Optional[WeatherInfo], playback_status: str = "Stopped") -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    now = datetime.now(ZoneInfo(TIMEZONE))
    text_center(draw, 26, now.strftime("%H:%M"), FONT_TIME, fill="white")
    text_center(draw, 86, now.strftime("%a %d %b"), FONT_SMALL, fill="#cfcfcf")
    text_center(draw, 108, get_location_label(), FONT_SMALL, fill="#9f9f9f")

    if weather:
        symbol = get_weather_symbol(weather.weather_code, weather.is_day)
        temp = f"{round(weather.temp_c):.0f}°C"
        label = WEATHER_CODES.get(weather.weather_code, f"Code {weather.weather_code}")
        text_center(draw, 138, symbol, FONT_WEATHER_ICON, fill="white")
        text_center(draw, 164, temp, FONT_WEATHER, fill="white")
        text_center(draw, 194, label, FONT_SMALL, fill="#cfcfcf")
    else:
        text_center(draw, 164, "Weather unavailable", FONT_SMALL, fill="#888888")
    text_center(draw, 218, f"Status: {playback_status}", FONT_SMALL, fill="#9f9f9f")
    return draw_button_labels(img, 0, fill="#8f8f8f", is_playing=False)


def render_now_playing(cover: Image.Image, track: PlexTrack) -> Image.Image:
    bg = fit_cover(cover, WIDTH, HEIGHT)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, HEIGHT - 82, WIDTH, HEIGHT), fill=(0, 0, 0, 150))
    composed = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(composed)

    text_x = 34
    text_max_width = WIDTH - text_x - 8
    title = truncate(draw, track.title, FONT_TRACK, text_max_width)
    artist = truncate(draw, track.artist, FONT_META, text_max_width)
    draw.text((text_x, HEIGHT - 74), title, font=FONT_TRACK, fill="white")
    draw.text((text_x, HEIGHT - 46), artist, font=FONT_META, fill="#dddddd")
    return draw_button_labels(composed, 0, is_playing=True)


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
    if now_ts - state.last_weather_fetch > WEATHER_REFRESH_SECONDS:
        state.last_weather = fetch_weather()
        state.last_weather_fetch = now_ts


def update_current_player_context(track: Optional[PlexTrack]) -> None:
    global CURRENT_TARGET_CLIENT_ID, CURRENT_PLAYER_ADDRESS, CURRENT_PLAYER_PORT
    CURRENT_TARGET_CLIENT_ID = track.target_client_identifier if track else None
    CURRENT_PLAYER_ADDRESS = track.player_address if track else None
    CURRENT_PLAYER_PORT = track.player_port if track else 32500


def render_playing_frame(state: LoopState, track: PlexTrack, now_ts: float) -> None:
    """Render now-playing state, refreshing cover art only when needed."""
    needs_refresh = (
        track.thumb_path != state.last_thumb_path
        or track.title != state.last_track_title
        or state.last_player_state != "playing"
    )
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
            f"needs_refresh={needs_refresh} needs_retry={needs_retry} "
            f"have_cover={state.cached_cover is not None}"
        ),
    )

    if not (needs_refresh or needs_retry):
        return

    if track.thumb_path != state.last_thumb_path:
        state.cached_cover = None

    if not state.cached_cover and track.thumb_path:
        state.cached_cover = fetch_plex_cover(track.thumb_path)

    if state.cached_cover:
        if try_write_framebuffer(render_now_playing(state.cached_cover, track), context="now-playing render"):
            state.last_thumb_path = track.thumb_path
            state.last_track_title = track.title
            state.last_player_state = "playing"
            state.next_cover_retry_ts = 0.0
            return
        write_fallback_placeholder("Render Error", context="now-playing render")
        return

    state.next_cover_retry_ts = now_ts + COVER_RETRY_SECONDS
    if try_write_framebuffer(create_error_placeholder("No Album Art"), context="no-album-art render"):
        state.last_thumb_path = track.thumb_path
        state.last_track_title = track.title
        state.last_player_state = "playing"


def render_idle_frame(state: LoopState, track: Optional[PlexTrack]) -> None:
    """Render idle/paused/stopped screen when minute or state changes."""
    minute_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    idle_state = track.state if track else "stopped"
    status_text = playback_status_text(idle_state)

    if minute_key == state.last_idle_minute and state.last_player_state == idle_state:
        return

    if try_write_framebuffer(render_idle(state.last_weather, playback_status=status_text), context="idle render"):
        state.last_idle_minute = minute_key
        state.last_player_state = idle_state
        state.last_thumb_path = None
        state.last_track_title = None
        state.cached_cover = None
        state.next_cover_retry_ts = 0.0
        return

    write_fallback_placeholder("Display Error", context="idle render")


def wait_for_next_cycle() -> None:
    """Wait for poll interval or immediate wake-up triggered by button commands."""
    REFRESH_EVENT.wait(timeout=POLL_SECONDS)
    if REFRESH_EVENT.is_set():
        REFRESH_EVENT.clear()
        time.sleep(0.5)


# Application entrypoint
def main():
    """Main app loop: fetch state, render frame, and sleep/wake for next cycle."""
    validate_startup()
    setup_gpio_buttons()

    state = LoopState()

    while True:
        try:
            now_ts = time.time()
            refresh_weather_if_due(state, now_ts)

            sessions = fetch_sessions_json()
            track = find_player_track(sessions) if sessions else None
            update_current_player_context(track)

            if track and track.state == "playing":
                render_playing_frame(state, track, now_ts)
            else:
                render_idle_frame(state, track)

        except KeyboardInterrupt:
            raise
        except PermissionError as exc:
            log_exception("main", "Permission error (framebuffer not writable?)", exc)
            time.sleep(5)
        except Exception as exc:
            log_exception("main", "Unhandled loop error", exc)
            time.sleep(5)

        wait_for_next_cycle()


if __name__ == "__main__":
    main()