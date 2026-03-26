"""Collect playback inputs for a single loop iteration."""

from dataclasses import dataclass
from typing import Callable, Optional

from core.models import LoopState, PlaybackSnapshot, PlexTrack, RuntimeState


@dataclass(frozen=True)
class PlaybackCollectorConfig:
    player_name: str
    plex_server: str
    plex_token: str
    http_timeout: int


@dataclass(frozen=True)
class PlaybackCollectorDeps:
    fetch_sessions_json: Callable[..., Optional[dict]]
    find_player_track: Callable[[dict, str], Optional[PlexTrack]]
    fetch_player_timeline_state: Callable[..., Optional[dict]]
    should_poll_timeline: Callable[[Optional[PlexTrack], Optional[str]], bool]
    log_warn: Callable[[str], None]
    log_error: Callable[[str], None]


@dataclass(frozen=True)
class CollectedPlayback:
    track: Optional[PlexTrack]
    snapshot: PlaybackSnapshot


def update_current_player_context(runtime_state: RuntimeState, track: Optional[PlexTrack]) -> None:
    if track:
        runtime_state.current_target_client_id = track.target_client_identifier
        runtime_state.current_player_address = track.player_address
        runtime_state.current_player_port = track.player_port
        runtime_state.current_playback_state = track.state
    else:
        runtime_state.current_playback_state = "unknown"


def collect_playback_snapshot(
    *,
    now_ts: float,
    loop_state: LoopState,
    runtime_state: RuntimeState,
    config: PlaybackCollectorConfig,
    deps: PlaybackCollectorDeps,
    enable_timeline_poll: bool = True,
) -> CollectedPlayback:
    sessions = deps.fetch_sessions_json(
        plex_server=config.plex_server,
        plex_token=config.plex_token,
        timeout=config.http_timeout,
        log_warn=deps.log_warn,
        log_error=deps.log_error,
    )
    track = deps.find_player_track(sessions, config.player_name) if sessions else None
    update_current_player_context(runtime_state, track)

    timeline_state: Optional[str] = None
    if enable_timeline_poll and deps.should_poll_timeline(track, loop_state.last_player_state):
        timeline = deps.fetch_player_timeline_state(
            player_addr=runtime_state.current_player_address,
            player_port=runtime_state.current_player_port,
            plex_token=config.plex_token,
            timeout=config.http_timeout,
            log_warn=deps.log_warn,
        )
        if timeline:
            timeline_state = str(timeline.get("state") or "").strip().lower() or None

    return CollectedPlayback(
        track=track,
        snapshot=PlaybackSnapshot(
            now_ts=now_ts,
            track=track,
            last_track_identity=loop_state.last_track_identity,
            last_player_state=loop_state.last_player_state,
            no_track_grace_until_ts=loop_state.no_track_grace_until_ts,
            force_idle_until_ts=runtime_state.force_idle_until_ts,
            pending_command=runtime_state.pending_command,
            timeline_state=timeline_state,
        ),
    )