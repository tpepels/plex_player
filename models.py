from dataclasses import dataclass
from typing import Optional

from PIL import Image


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


@dataclass
class LoopState:
    """Mutable state used by the main render loop between iterations."""

    last_weather: Optional[WeatherInfo] = None
    last_weather_fetch: float = 0.0
    last_thumb_path: Optional[str] = None
    last_track_title: Optional[str] = None
    last_player_state: Optional[str] = None
    last_idle_minute: Optional[str] = None
    cached_cover: Optional[Image.Image] = None
    next_cover_retry_ts: float = 0.0
