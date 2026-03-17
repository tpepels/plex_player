"""Centralized rule engine for playback transitions and button intent.

This module contains policy decisions only; rendering and I/O stay elsewhere.
"""

from typing import Optional

from models import LoopState, PlaybackSnapshot, PlexTrack, RuntimeState, TransitionDecision, TransitionMode


def _normalized_state(track: Optional[PlexTrack]) -> str:
    if not track:
        return "unknown"
    return str(track.state or "unknown").strip().lower()


def _track_identity(track: PlexTrack) -> str:
    """Return a stable identity key for elapsed/progress anchoring.

    Uses multiple fields because titles can repeat across albums/versions.
    """

    return "|".join(
        [
            str(track.title or ""),
            str(track.artist or ""),
            str(track.album or ""),
            str(track.thumb_path or ""),
            str(track.duration_ms if track.duration_ms is not None else ""),
        ]
    )


def apply_button_rules(
    runtime_state: RuntimeState,
    action: str,
    *,
    now_ts: float,
    toast_duration_seconds: float,
    stop_force_idle_seconds: float,
) -> None:
    """Apply local button-intent rules to runtime state."""

    toast_duration_seconds = max(0.0, float(toast_duration_seconds))
    stop_force_idle_seconds = max(0.0, float(stop_force_idle_seconds))

    if action == "next":
        runtime_state.toast_text = "Skipped"
    elif action == "stop":
        runtime_state.toast_text = "Stopped"
        runtime_state.force_idle_until_ts = now_ts + stop_force_idle_seconds
    elif action == "play_pause":
        # A local toggle intent should cancel previous forced-idle window.
        runtime_state.force_idle_until_ts = 0.0
        runtime_state.toast_text = "Paused" if runtime_state.current_playback_state == "playing" else "Playing"
    else:
        runtime_state.toast_text = "Command sent"

    runtime_state.toast_until_ts = now_ts + toast_duration_seconds


def resolve_transition(snapshot: PlaybackSnapshot, *, no_track_grace_seconds: float) -> TransitionDecision:
    """Resolve transition rules from Plex snapshot plus local button intent."""

    no_track_grace_seconds = max(0.0, float(no_track_grace_seconds))
    track_state = _normalized_state(snapshot.track)

    if snapshot.now_ts < snapshot.force_idle_until_ts:
        return TransitionDecision(mode=TransitionMode.IDLE, reason="force_idle_window", idle_track=None)

    if snapshot.track and track_state == "playing":
        return TransitionDecision(
            mode=TransitionMode.PLAYING,
            reason="plex_playing",
            set_no_track_grace_until_ts=snapshot.now_ts + no_track_grace_seconds,
            clear_force_idle=True,
        )

    if (
        not snapshot.track
        and str(snapshot.last_player_state or "").strip().lower() == "playing"
        and snapshot.now_ts < snapshot.no_track_grace_until_ts
    ):
        return TransitionDecision(mode=TransitionMode.HOLD, reason="transient_no_track_gap")

    return TransitionDecision(mode=TransitionMode.IDLE, reason="default_idle", idle_track=snapshot.track)


def compute_display_elapsed_ms(state: LoopState, track: PlexTrack, now_ts: float) -> Optional[int]:
    """Estimate elapsed playback between coarse Plex metadata updates."""

    identity = _track_identity(track)
    if identity != state.last_track_identity:
        state.last_reported_elapsed_ms = None
        state.elapsed_anchor_ms = None
        state.elapsed_anchor_ts = now_ts
        state.last_track_identity = identity

    reported_ms = track.elapsed_ms
    if reported_ms is not None:
        try:
            reported_ms = max(0, int(reported_ms))
        except (TypeError, ValueError):
            reported_ms = None
    duration_ms = track.duration_ms
    if duration_ms is not None:
        try:
            duration_ms = max(0, int(duration_ms))
        except (TypeError, ValueError):
            duration_ms = None

    if reported_ms is not None:
        if state.last_reported_elapsed_ms is None or reported_ms != state.last_reported_elapsed_ms:
            state.elapsed_anchor_ms = reported_ms
            state.elapsed_anchor_ts = now_ts
        state.last_reported_elapsed_ms = reported_ms

    if state.elapsed_anchor_ms is None:
        return reported_ms

    estimated = state.elapsed_anchor_ms + int(max(0.0, now_ts - state.elapsed_anchor_ts) * 1000)
    if duration_ms and duration_ms > 0:
        return max(0, min(estimated, duration_ms))
    return max(0, estimated)
