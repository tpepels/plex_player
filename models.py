"""Shared data models for Plex LCD runtime.

Design assumptions:
- These models are intentionally lightweight containers, not behavior objects.
- Most fields are optional because Plex/Open-Meteo payloads vary by endpoint/client.
- `LoopState` is strictly render-loop cache state and must never be treated as source-of-truth media state.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PIL import Image


class TransitionMode(str, Enum):
    """Valid transition outputs for render routing."""

    PLAYING = "playing"
    HOLD = "hold"
    IDLE = "idle"


@dataclass
class WeatherInfo:
    """Weather payload normalized for idle-screen rendering.

    Assumptions:
    - Current values are expected to exist when fetch succeeds.
    - Daily/hourly fields may be absent and should be rendered defensively.
    """

    temp_c: float
    weather_code: int
    is_day: int
    humidity_pct: Optional[int] = None
    temp_min_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    next_hour_temp_c: Optional[float] = None
    next_hour_weather_code: Optional[int] = None


@dataclass
class PlexTrack:
    """Track/session payload normalized from Plex `status/sessions`.

    Assumptions:
    - `state` mirrors Plex raw state (not a synthetic app-level state).
    - `elapsed_ms`/`duration_ms` may be missing for some clients/streams.
    """

    title: str
    artist: str
    album: str
    thumb_path: Optional[str]
    state: str
    target_client_identifier: Optional[str]
    player_address: Optional[str] = None
    player_port: int = 32500
    elapsed_ms: Optional[int] = None
    duration_ms: Optional[int] = None


@dataclass
class LoopState:
    """Mutable state used by the main render loop between iterations."""

    last_weather: Optional[WeatherInfo] = None
    last_weather_fetch: float = 0.0
    last_thumb_path: Optional[str] = None
    last_track_title: Optional[str] = None
    last_track_identity: Optional[str] = None
    last_player_state: Optional[str] = None
    last_idle_minute: Optional[str] = None
    last_elapsed_second: Optional[int] = None
    last_reported_elapsed_ms: Optional[int] = None
    elapsed_anchor_ms: Optional[int] = None
    elapsed_anchor_ts: float = 0.0
    last_toast_visible: Optional[bool] = None
    cached_cover: Optional[Image.Image] = None
    next_cover_retry_ts: float = 0.0
    no_track_grace_until_ts: float = 0.0


@dataclass
class RuntimeState:
    """Mutable cross-cutting app state used by callbacks and UI overlays.

    Assumptions:
    - This holds fast-changing runtime values that are not persisted.
    - A single instance is shared by loop + button callbacks.
    """

    current_target_client_id: Optional[str] = None
    current_player_address: Optional[str] = None
    current_player_port: int = 32500
    current_playback_state: str = "unknown"
    toast_text: Optional[str] = None
    toast_until_ts: float = 0.0
    force_idle_until_ts: float = 0.0
    pending_command: Optional["PendingCommand"] = None
    command_counter: int = 1


@dataclass
class PendingCommand:
    """Local command intent awaiting Plex-state confirmation."""

    action: str
    command_id: int
    issued_ts: float
    deadline_ts: float


@dataclass
class PlaybackSnapshot:
    """Single-loop snapshot used to resolve render transitions."""

    now_ts: float
    track: Optional[PlexTrack]
    last_player_state: Optional[str]
    no_track_grace_until_ts: float
    force_idle_until_ts: float
    pending_command: Optional[PendingCommand]
    timeline_state: Optional[str] = None


@dataclass
class TransitionDecision:
    """Resolved transition output consumed by the main loop."""

    mode: TransitionMode
    reason: str
    idle_track: Optional[PlexTrack] = None
    set_no_track_grace_until_ts: Optional[float] = None
    clear_force_idle: bool = False
    clear_pending_command: bool = False
