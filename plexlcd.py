#!/usr/bin/env python3
import io
import os
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from zoneinfo import ZoneInfo

try:
    from gpiozero import Button
except Exception:
    Button = None


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


PLEX_SERVER = env("PLEX_SERVER", "http://plex.local:32400").rstrip("/")
PLEX_TOKEN = env("PLEX_TOKEN", "")
PLAYER_NAME = env("PLAYER_NAME", "Plexamp Pi Zero")
LATITUDE_RAW = env("LATITUDE", "0.0000")
LONGITUDE_RAW = env("LONGITUDE", "0.0000")
TIMEZONE = env("TIMEZONE", "UTC")
FB_DEVICE = env("FB_DEVICE", "/dev/fb1")
WIDTH_RAW = env("WIDTH", "320")
HEIGHT_RAW = env("HEIGHT", "240")
BUTTONS_ENABLED_RAW = env("BUTTONS_ENABLED", "0")
BUTTON_PLAY_PAUSE_PIN_RAW = env("BUTTON_PLAY_PAUSE_PIN", "23")
BUTTON_STOP_PIN_RAW = env("BUTTON_STOP_PIN", "24")
BUTTON_NEXT_PIN_RAW = env("BUTTON_NEXT_PIN", "25")
BUTTON_BOUNCE_TIME_RAW = env("BUTTON_BOUNCE_TIME", "0.15")
BUTTON_LABEL_PLAY_Y_PERCENT_RAW = env("BUTTON_LABEL_PLAY_Y_PERCENT", "20")
BUTTON_LABEL_STOP_Y_PERCENT_RAW = env("BUTTON_LABEL_STOP_Y_PERCENT", "40")
BUTTON_LABEL_NEXT_Y_PERCENT_RAW = env("BUTTON_LABEL_NEXT_Y_PERCENT", "60")
POLL_SECONDS_RAW = env("POLL_SECONDS", "3")
WEATHER_REFRESH_SECONDS_RAW = env("WEATHER_REFRESH_SECONDS", "900")
DISPLAY_X_SHIFT_RAW = env("DISPLAY_X_SHIFT", "0")
CONTROLLER_CLIENT_ID = env("CONTROLLER_CLIENT_ID", f"plexlcd-{socket.gethostname()}")

LATITUDE: float = 0.0
LONGITUDE: float = 0.0
WIDTH: int = 320
HEIGHT: int = 240
BUTTONS_ENABLED: bool = False
BUTTON_PLAY_PAUSE_PIN: int = 23
BUTTON_STOP_PIN: int = 24
BUTTON_NEXT_PIN: int = 25
BUTTON_BOUNCE_TIME: float = 0.15
BUTTON_LABEL_PLAY_Y_PERCENT: int = 20
BUTTON_LABEL_STOP_Y_PERCENT: int = 40
BUTTON_LABEL_NEXT_Y_PERCENT: int = 60
POLL_SECONDS: int = 3
WEATHER_REFRESH_SECONDS: int = 900
DISPLAY_X_SHIFT: int = 0
HTTP_TIMEOUT = 10
COVER_RETRY_SECONDS = 20
BUTTON_DEVICES = []
CURRENT_TARGET_CLIENT_ID: Optional[str] = None
CURRENT_PLAYER_ADDRESS: Optional[str] = None
CURRENT_PLAYER_PORT: int = 32500
COMMAND_COUNTER = 1
REFRESH_EVENT = __import__('threading').Event()

FONT_PATH_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


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


@dataclass
class WeatherInfo:
    temp_c: float
    weather_code: int
    is_day: int


@dataclass
class PlexTrack:
    title: str
    artist: str
    album: str
    thumb_path: Optional[str]
    state: str
    target_client_identifier: Optional[str]
    player_address: Optional[str] = None
    player_port: int = 32500


def normalize_playback_state(item: dict) -> str:
    """Normalize Plex session/player state to a simple playing vs idle model."""
    player = item.get("Player", {})
    session = item.get("Session", {})

    raw_states = [
        str(player.get("state") or "").strip().lower(),
        str(session.get("state") or "").strip().lower(),
    ]

    if "playing" in raw_states:
        return "playing"
    if any(state in {"paused", "stopped", "buffering", "idle", "none"} for state in raw_states):
        return "idle"
    return raw_states[0] or raw_states[1] or "unknown"


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


FONT_TIME = load_font(FONT_PATH_BOLD, 54)
FONT_WEATHER = load_font(FONT_PATH_REGULAR, 24)
FONT_SMALL = load_font(FONT_PATH_REGULAR, 18)
FONT_TRACK = load_font(FONT_PATH_BOLD, 22)
FONT_META = load_font(FONT_PATH_REGULAR, 18)
FONT_LABEL = load_font(FONT_PATH_REGULAR, 12)


def validate_startup():
    """Validate configuration at startup. Exit with error if critical settings missing."""
    global LATITUDE, LONGITUDE, WIDTH, HEIGHT, BUTTONS_ENABLED, BUTTON_PLAY_PAUSE_PIN, BUTTON_STOP_PIN, BUTTON_NEXT_PIN, BUTTON_BOUNCE_TIME, BUTTON_LABEL_PLAY_Y_PERCENT, BUTTON_LABEL_STOP_Y_PERCENT, BUTTON_LABEL_NEXT_Y_PERCENT, POLL_SECONDS, WEATHER_REFRESH_SECONDS, DISPLAY_X_SHIFT
    errors = []
    
    if not PLEX_TOKEN or PLEX_TOKEN.strip() == "":
        errors.append("PLEX_TOKEN not set or empty")
    
    if not os.path.exists(FB_DEVICE):
        errors.append(f"FB_DEVICE '{FB_DEVICE}' does not exist")
    elif not os.access(FB_DEVICE, os.W_OK):
        errors.append(f"FB_DEVICE '{FB_DEVICE}' is not writable (need root or group membership)")
    
    try:
        LATITUDE = float(LATITUDE_RAW)
        LONGITUDE = float(LONGITUDE_RAW)
    except (TypeError, ValueError):
        errors.append("LATITUDE and LONGITUDE must be valid numbers")

    try:
        WIDTH = int(WIDTH_RAW)
        HEIGHT = int(HEIGHT_RAW)
        BUTTON_PLAY_PAUSE_PIN = int(BUTTON_PLAY_PAUSE_PIN_RAW)
        BUTTON_STOP_PIN = int(BUTTON_STOP_PIN_RAW)
        BUTTON_NEXT_PIN = int(BUTTON_NEXT_PIN_RAW)
        BUTTON_BOUNCE_TIME = float(BUTTON_BOUNCE_TIME_RAW)
        BUTTON_LABEL_PLAY_Y_PERCENT = int(BUTTON_LABEL_PLAY_Y_PERCENT_RAW)
        BUTTON_LABEL_STOP_Y_PERCENT = int(BUTTON_LABEL_STOP_Y_PERCENT_RAW)
        BUTTON_LABEL_NEXT_Y_PERCENT = int(BUTTON_LABEL_NEXT_Y_PERCENT_RAW)
        POLL_SECONDS = int(POLL_SECONDS_RAW)
        WEATHER_REFRESH_SECONDS = int(WEATHER_REFRESH_SECONDS_RAW)
        DISPLAY_X_SHIFT = int(DISPLAY_X_SHIFT_RAW)
    except (TypeError, ValueError):
        errors.append("Display/button timing settings must be valid numbers")

    BUTTONS_ENABLED = BUTTONS_ENABLED_RAW.strip().lower() in {"1", "true", "yes", "on"}

    if isinstance(WIDTH, int) and WIDTH <= 0:
        errors.append("WIDTH must be > 0")
    if isinstance(HEIGHT, int) and HEIGHT <= 0:
        errors.append("HEIGHT must be > 0")
    if isinstance(POLL_SECONDS, int) and POLL_SECONDS < 1:
        errors.append("POLL_SECONDS must be >= 1")
    if isinstance(WEATHER_REFRESH_SECONDS, int) and WEATHER_REFRESH_SECONDS < 60:
        errors.append("WEATHER_REFRESH_SECONDS must be >= 60")
    if isinstance(DISPLAY_X_SHIFT, int) and abs(DISPLAY_X_SHIFT) >= max(1, WIDTH):
        errors.append("DISPLAY_X_SHIFT must be smaller than WIDTH")
    for label_name, label_percent in (
        ("BUTTON_LABEL_PLAY_Y_PERCENT", BUTTON_LABEL_PLAY_Y_PERCENT),
        ("BUTTON_LABEL_STOP_Y_PERCENT", BUTTON_LABEL_STOP_Y_PERCENT),
        ("BUTTON_LABEL_NEXT_Y_PERCENT", BUTTON_LABEL_NEXT_Y_PERCENT),
    ):
        if isinstance(label_percent, int) and not 0 <= label_percent <= 100:
            errors.append(f"{label_name} must be between 0 and 100")
    if BUTTONS_ENABLED and Button is None:
        errors.append("BUTTONS_ENABLED is set but gpiozero is not installed")

    try:
        ZoneInfo(TIMEZONE)
    except Exception:
        errors.append(f"TIMEZONE '{TIMEZONE}' is invalid")
    
    if errors:
        print("[startup] Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    
    print("[startup] Configuration validated successfully")


def next_command_id() -> int:
    global COMMAND_COUNTER
    COMMAND_COUNTER += 1
    return COMMAND_COUNTER


def send_plex_playback_command(action: str):
    target_client_id = CURRENT_TARGET_CLIENT_ID
    player_addr = CURRENT_PLAYER_ADDRESS
    player_port = CURRENT_PLAYER_PORT

    if not target_client_id:
        print(f"[buttons] Ignoring {action}: no active Plex target client", file=sys.stderr, flush=True)
        return

    endpoint_map = {
        "play_pause": "playPause",
        "stop": "stop",
        "next": "skipNext",
    }
    endpoint = endpoint_map.get(action)
    if not endpoint:
        return

    # Send directly to the Plexamp player, not via the server
    if player_addr:
        base_url = f"http://{player_addr}:{player_port}"
    else:
        base_url = PLEX_SERVER
        print(f"[buttons] WARNING: no player address known, falling back to server URL", file=sys.stderr, flush=True)

    url = f"{base_url}/player/playback/{endpoint}"
    cmd_id = next_command_id()
    print(f"[buttons] {action} → GET {url}  commandID={cmd_id}", file=sys.stderr, flush=True)
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
        print(f"[buttons] Response {resp.status_code}: {resp.text[:200]!r}", file=sys.stderr, flush=True)
        resp.raise_for_status()
        print(f"[buttons] Sent {action} OK", file=sys.stderr, flush=True)
        REFRESH_EVENT.set()
    except Exception as exc:
        print(f"[buttons] Failed to send {action}: {exc}", file=sys.stderr, flush=True)


def setup_gpio_buttons():
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

    print(
        "[buttons] Enabled GPIO buttons: "
        f"play/pause={BUTTON_PLAY_PAUSE_PIN}, stop={BUTTON_STOP_PIN}, next={BUTTON_NEXT_PIN}"
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

    draw = ImageDraw.Draw(img)
    labels = ["▌▌" if is_playing else "▶", "■", ">>|"]
    x = 6
    y_positions = [
        HEIGHT * BUTTON_LABEL_PLAY_Y_PERCENT // 100,
        HEIGHT * BUTTON_LABEL_STOP_Y_PERCENT // 100,
        HEIGHT * BUTTON_LABEL_NEXT_Y_PERCENT // 100,
    ]

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
                print(f"[weather] Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                print(f"[weather] Failed after {max_retries} attempts: {exc}")
    return None


def fetch_sessions_json() -> Optional[dict]:
    if not PLEX_TOKEN:
        print("[plex] missing PLEX_TOKEN", file=sys.stderr)
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
                print(f"[plex] Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                print(f"[plex] sessions: Failed after {max_retries} attempts: {exc}")
    return None


def find_player_track(data: dict) -> Optional[PlexTrack]:
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
                print(f"[plex] Cover attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                print(f"[plex] cover: Failed after {max_retries} attempts: {exc}")
    return None


def render_idle(weather: Optional[WeatherInfo]) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    now = datetime.now(ZoneInfo(TIMEZONE))
    text_center(draw, 34, now.strftime("%H:%M"), FONT_TIME, fill="white")
    text_center(draw, 100, now.strftime("%a %d %b"), FONT_SMALL, fill="#cfcfcf")

    if weather:
        temp = f"{round(weather.temp_c):.0f}°C"
        label = WEATHER_CODES.get(weather.weather_code, f"Code {weather.weather_code}")
        text_center(draw, 150, temp, FONT_WEATHER, fill="white")
        text_center(draw, 182, label, FONT_SMALL, fill="#cfcfcf")
    else:
        text_center(draw, 165, "Weather unavailable", FONT_SMALL, fill="#888888")
    return draw_button_labels(img, 0, fill="#8f8f8f", is_playing=False)


def render_now_playing(cover: Image.Image, track: PlexTrack) -> Image.Image:
    bg = fit_cover(cover, WIDTH, HEIGHT)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, HEIGHT - 82, WIDTH, HEIGHT), fill=(0, 0, 0, 150))
    composed = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(composed)

    title = truncate(draw, track.title, FONT_TRACK, WIDTH - 16)
    artist = truncate(draw, track.artist, FONT_META, WIDTH - 16)
    draw.text((8, HEIGHT - 74), title, font=FONT_TRACK, fill="white")
    draw.text((8, HEIGHT - 46), artist, font=FONT_META, fill="#dddddd")
    return draw_button_labels(composed, 0, is_playing=True)


def main():
    validate_startup()
    setup_gpio_buttons()
    
    last_weather = None
    last_weather_fetch = 0.0
    last_thumb_path = None
    last_player_state = None
    last_idle_minute = None
    cached_cover = None
    next_cover_retry_ts = 0.0

    while True:
        try:
            now_ts = time.time()
            
            # Refresh weather periodically
            if now_ts - last_weather_fetch > WEATHER_REFRESH_SECONDS:
                last_weather = fetch_weather()
                last_weather_fetch = now_ts

            sessions = fetch_sessions_json()
            track = find_player_track(sessions) if sessions else None
            global CURRENT_TARGET_CLIENT_ID, CURRENT_PLAYER_ADDRESS, CURRENT_PLAYER_PORT
            CURRENT_TARGET_CLIENT_ID = track.target_client_identifier if track else None
            CURRENT_PLAYER_ADDRESS = track.player_address if track else None
            CURRENT_PLAYER_PORT = track.player_port if track else 32500

            # Player is actively playing
            if track and track.state == "playing":
                # Fetch on track/state change, or retry after cooldown when cover fetch previously failed.
                needs_refresh = track.thumb_path != last_thumb_path or last_player_state != "playing"
                needs_retry = (
                    not cached_cover
                    and track.thumb_path == last_thumb_path
                    and now_ts >= next_cover_retry_ts
                )

                if needs_refresh or needs_retry:
                    cached_cover = None
                    if track.thumb_path:
                        cached_cover = fetch_plex_cover(track.thumb_path)
                    
                    if cached_cover:
                        try:
                            write_framebuffer(render_now_playing(cached_cover, track))
                            last_thumb_path = track.thumb_path
                            last_player_state = "playing"
                            next_cover_retry_ts = 0.0
                        except Exception as e:
                            print(f"[render] Failed to render now-playing: {e}")
                            img = create_error_placeholder("Render Error")
                            try:
                                write_framebuffer(img)
                            except Exception as e2:
                                print(f"[framebuffer] Failed to write error placeholder: {e2}")
                    else:
                        # No cover available, show placeholder and back off retries.
                        next_cover_retry_ts = now_ts + COVER_RETRY_SECONDS
                        img = create_error_placeholder("No Album Art")
                        try:
                            write_framebuffer(img)
                            last_thumb_path = track.thumb_path
                            last_player_state = "playing"
                        except Exception as e:
                            print(f"[framebuffer] Failed to write placeholder: {e}")
            
            # Player is paused, stopped, or no track found
            else:
                minute_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
                if minute_key != last_idle_minute or last_player_state == "playing":
                    try:
                        write_framebuffer(render_idle(last_weather))
                        last_idle_minute = minute_key
                        last_player_state = "idle"
                        last_thumb_path = None
                        cached_cover = None
                        next_cover_retry_ts = 0.0
                    except Exception as e:
                        print(f"[render] Failed to render idle screen: {e}")
                        img = create_error_placeholder("Display Error")
                        try:
                            write_framebuffer(img)
                        except Exception as e2:
                            print(f"[framebuffer] Failed to write error placeholder: {e2}")
        
        except KeyboardInterrupt:
            raise
        except PermissionError as e:
            print(f"[main] Permission error (framebuffer not writable?): {e}", file=sys.stderr)
            time.sleep(5)
        except Exception as exc:
            print(f"[main] {exc}", file=sys.stderr)
            time.sleep(5)

        REFRESH_EVENT.wait(timeout=POLL_SECONDS)
        if REFRESH_EVENT.is_set():
            REFRESH_EVENT.clear()
            time.sleep(0.5)  # Give Plexamp time to apply the command before re-polling


if __name__ == "__main__":
    main()