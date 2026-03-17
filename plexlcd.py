#!/usr/bin/env python3
import io
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from zoneinfo import ZoneInfo


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


PLEX_SERVER = env("PLEX_SERVER", "http://192.168.1.200:32400").rstrip("/")
PLEX_TOKEN = env("PLEX_TOKEN", "")
PLAYER_NAME = env("PLAYER_NAME", "Plexamp Pi Zero")
LATITUDE = float(env("LATITUDE", "41.1579"))
LONGITUDE = float(env("LONGITUDE", "-8.6291"))
TIMEZONE = env("TIMEZONE", "Europe/Lisbon")
FB_DEVICE = env("FB_DEVICE", "/dev/fb1")
WIDTH = int(env("WIDTH", "320"))
HEIGHT = int(env("HEIGHT", "240"))
POLL_SECONDS = int(env("POLL_SECONDS", "3"))
WEATHER_REFRESH_SECONDS = int(env("WEATHER_REFRESH_SECONDS", "900"))
HTTP_TIMEOUT = 10

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


def text_center(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill="white"):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = max(0, (WIDTH - w) // 2)
    draw.text((x, y), text, font=font, fill=fill)


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


def rgb888_to_rgb565_bytes(img: Image.Image) -> bytes:
    if img.mode != "RGB":
        img = img.convert("RGB")
    pixels = img.load()
    out = bytearray(WIDTH * HEIGHT * 2)
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = pixels[x, y]
            value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[i] = value & 0xFF
            out[i + 1] = (value >> 8) & 0xFF
            i += 2
    return bytes(out)


def write_framebuffer(img: Image.Image):
    raw = rgb888_to_rgb565_bytes(img)
    with open(FB_DEVICE, "wb", buffering=0) as fb:
        fb.write(raw)


def fetch_weather() -> Optional[WeatherInfo]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        "&current=temperature_2m,weather_code,is_day"
        f"&timezone={TIMEZONE}"
    )
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
        print(f"[weather] {exc}")
        return None


def fetch_sessions_json() -> Optional[dict]:
    if not PLEX_TOKEN:
        print("[plex] missing PLEX_TOKEN")
        return None
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
        print(f"[plex] sessions: {exc}")
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
            return PlexTrack(
                title=item.get("title", "Unknown Track"),
                artist=item.get("grandparentTitle", "Unknown Artist"),
                album=item.get("parentTitle", "Unknown Album"),
                thumb_path=item.get("thumb"),
                state=player.get("state", "unknown"),
            )
    return None


def fetch_plex_cover(thumb_path: str) -> Optional[Image.Image]:
    if not thumb_path:
        return None
    try:
        r = requests.get(
            f"{PLEX_SERVER}/photo/:/transcode",
            params={
                "url": f"{PLEX_SERVER}{thumb_path}",
                "width": WIDTH,
                "height": HEIGHT,
                "minSize": 1,
                "upscale": 1,
                "X-Plex-Token": PLEX_TOKEN,
            },
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as exc:
        print(f"[plex] cover: {exc}")
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
    return img


def render_now_playing(cover: Image.Image, track: PlexTrack) -> Image.Image:
    bg = fit_cover(cover, WIDTH, HEIGHT)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, HEIGHT - 68, WIDTH, HEIGHT), fill=(0, 0, 0, 150))
    composed = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(composed)

    title = truncate(draw, track.title, FONT_TRACK, WIDTH - 16)
    artist = truncate(draw, track.artist, FONT_META, WIDTH - 16)
    draw.text((8, HEIGHT - 60), title, font=FONT_TRACK, fill="white")
    draw.text((8, HEIGHT - 32), artist, font=FONT_META, fill="#dddddd")
    return composed


def main():
    last_weather = None
    last_weather_fetch = 0.0
    last_thumb_path = None
    last_state = None
    last_idle_minute = None
    cached_cover = None

    while True:
        try:
            now_ts = time.time()
            if now_ts - last_weather_fetch > WEATHER_REFRESH_SECONDS:
                last_weather = fetch_weather()
                last_weather_fetch = now_ts

            sessions = fetch_sessions_json()
            track = find_player_track(sessions) if sessions else None

            if track and track.state == "playing":
                if track.thumb_path != last_thumb_path or last_state != "playing" or cached_cover is None:
                    cover = fetch_plex_cover(track.thumb_path) if track.thumb_path else None
                    if cover:
                        cached_cover = cover
                        write_framebuffer(render_now_playing(cover, track))
                        last_thumb_path = track.thumb_path
                        last_state = "playing"
            else:
                minute_key = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
                if minute_key != last_idle_minute or last_state == "playing":
                    write_framebuffer(render_idle(last_weather))
                    last_idle_minute = minute_key
                    last_state = "idle"
                    last_thumb_path = None
                    cached_cover = None
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[main] {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()