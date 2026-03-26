"""Centralized rule engine for playback transitions and button intent.

This module contains policy decisions only; rendering and I/O stay elsewhere.
"""

from typing import Optional
from dataclasses import replace

from core.models import LoopState, PendingCommand, PlaybackSnapshot, PlexTrack, RuntimeState, TransitionDecision, TransitionMode


def _normalized_state(track: Optional[PlexTrack]) -> str:
    if not track:
        return "unknown"
    return str(track.state or "unknown").strip().lower()


def _effective_playback_state(track: Optional[PlexTrack], timeline_state: str) -> str:
    """Resolve effective playback state with explicit source precedence."""

    if timeline_state in {"playing", "paused", "stopped", "none"}:
        return "stopped" if timeline_state == "none" else timeline_state

    if not track:
        return "unknown"

    player_state = str(track.player_state_raw or "").strip().lower()
    session_state = str(track.session_state_raw or "").strip().lower()
    base_state = _normalized_state(track)

    # Prefer explicit non-playing session state over stale player "playing".
    if player_state == "playing" and session_state in {"paused", "stopped"}:
        base_state = session_state

    # Queue-end stale-playing guard: if almost at duration end, treat as stopped.
    if base_state == "playing" and track.duration_ms and track.elapsed_ms is not None:
        if track.elapsed_ms >= max(0, track.duration_ms - 1500):
            return "stopped"

    return base_state


def _normalized_timeline_state(state: Optional[str]) -> str:
    return str(state or "").strip().lower()


def _track_identity(track: PlexTrack) -> str:
    """Stable identity key for elapsed/progress anchoring."""
    return "|".join([
        str(track.title or ""), str(track.artist or ""), str(track.album or ""),
        str(track.thumb_path or ""), str(track.duration_ms if track.duration_ms is not None else ""),
    ])


def _idle_track_from_effective_state(track: Optional[PlexTrack], effective_state: str) -> Optional[PlexTrack]:
    """Map effective playback state to the idle payload expected by the renderer."""

    if not track:
        return None
    if effective_state == "paused":
        return replace(track, state="paused")
    if effective_state == "stopped":
        return None
    return track


def apply_button_rules(
    runtime_state: RuntimeState,
    action: str,
    *,
    command_id: int,
    now_ts: float,
    toast_duration_seconds: float,
    stop_force_idle_seconds: float,
    confirm_timeout_seconds: float,
) -> None:
    """Apply local button-intent rules to runtime state."""

    toast_duration_seconds = max(0.0, float(toast_duration_seconds))
    stop_force_idle_seconds = max(0.0, float(stop_force_idle_seconds))
    confirm_timeout_seconds = max(0.5, float(confirm_timeout_seconds))

    if action == "next":
        runtime_state.toast_text = "Skipped"
        expected_states = ("playing",)
    elif action == "stop":
        runtime_state.toast_text = "Stopped"
        runtime_state.force_idle_until_ts = now_ts + stop_force_idle_seconds
        expected_states = ("stopped", "paused", "none", "unknown")
    elif action == "play_pause":
        # A local toggle intent should cancel previous forced-idle window.
        runtime_state.force_idle_until_ts = 0.0
        runtime_state.toast_text = "Paused" if runtime_state.current_playback_state == "playing" else "Playing"
        expected_states = ("paused", "stopped") if runtime_state.current_playback_state == "playing" else ("playing",)
    else:
        runtime_state.toast_text = "Command sent"
        expected_states = ()

    runtime_state.toast_until_ts = now_ts + toast_duration_seconds
    runtime_state.pending_command = PendingCommand(
        action=action,
        command_id=int(command_id),
        issued_ts=now_ts,
        deadline_ts=now_ts + confirm_timeout_seconds,
        expected_states=expected_states,
    )


def should_poll_timeline(track: Optional[PlexTrack], last_player_state: Optional[str]) -> bool:
    """Policy helper to decide whether direct timeline polling is useful this cycle."""

    if track and str(track.state or "").strip().lower() == "playing":
        return True
    return str(last_player_state or "").strip().lower() == "playing"


def resolve_transition(snapshot: PlaybackSnapshot, *, no_track_grace_seconds: float) -> TransitionDecision:
    """Resolve transition rules from Plex snapshot plus local button intent."""

    no_track_grace_seconds = max(0.0, float(no_track_grace_seconds))
    timeline_state = _normalized_timeline_state(snapshot.timeline_state)
    track_state = _effective_playback_state(snapshot.track, timeline_state)
    clear_pending = False

    pending = snapshot.pending_command
    if pending and snapshot.now_ts >= pending.deadline_ts:
        clear_pending = True
        pending = None

    # Two-phase confirmation for STOP: once Plex is no longer playing, clear pending.
    if pending and pending.action == "stop" and track_state != "playing":
        return TransitionDecision(
            mode=TransitionMode.IDLE,
            reason="pending_stop_confirmed",
            idle_track=None,
            clear_pending_command=True,
        )

    # Two-phase confirmation for NEXT: require track identity change while still playing.
    if pending and pending.action == "next" and snapshot.track:
        current_identity = _track_identity(snapshot.track)
        if snapshot.last_track_identity and current_identity != snapshot.last_track_identity:
            clear_pending = True

    # Two-phase confirmation for PLAY_PAUSE: confirm expected resulting playback state.
    if pending and pending.action == "play_pause" and pending.expected_states:
        if track_state in pending.expected_states:
            clear_pending = True

    # Timeline tie-break rule is already part of effective state. Use it here to
    # shape idle-track payload for status display.
    if timeline_state in {"paused", "stopped", "none"}:
        if snapshot.track:
            idle_track = _idle_track_from_effective_state(snapshot.track, track_state)
            return TransitionDecision(
                mode=TransitionMode.IDLE,
                reason="timeline_not_playing",
                idle_track=idle_track,
                clear_pending_command=clear_pending,
            )
        if (
            not snapshot.track
            and str(snapshot.last_player_state or "").strip().lower() == "playing"
        ):
            return TransitionDecision(
                mode=TransitionMode.IDLE,
                reason="timeline_not_playing",
                idle_track=None,
                set_no_track_grace_until_ts=0.0,
                clear_pending_command=clear_pending,
            )

    if snapshot.now_ts < snapshot.force_idle_until_ts:
        return TransitionDecision(
            mode=TransitionMode.IDLE,
            reason="force_idle_window",
            idle_track=None,
            clear_pending_command=clear_pending,
        )

    if snapshot.track and track_state == "playing":
        return TransitionDecision(
            mode=TransitionMode.PLAYING,
            reason="plex_playing",
            set_no_track_grace_until_ts=snapshot.now_ts + no_track_grace_seconds,
            clear_force_idle=True,
            clear_pending_command=clear_pending,
        )

    if (
        not snapshot.track
        and str(snapshot.last_player_state or "").strip().lower() == "playing"
        and snapshot.now_ts < snapshot.no_track_grace_until_ts
    ):
        return TransitionDecision(
            mode=TransitionMode.HOLD,
            reason="transient_no_track_gap",
            clear_pending_command=clear_pending,
        )

    return TransitionDecision(
        mode=TransitionMode.IDLE,
        reason="default_idle",
        idle_track=_idle_track_from_effective_state(snapshot.track, track_state),
        clear_pending_command=clear_pending,
    )


def apply_transition_decision(runtime_state: RuntimeState, loop_state: LoopState, decision: TransitionDecision) -> None:
    """Reducer-style state application for transition side effects."""

    if decision.clear_force_idle:
        runtime_state.force_idle_until_ts = 0.0
    if decision.clear_pending_command:
        runtime_state.pending_command = None
    if decision.set_no_track_grace_until_ts is not None:
        loop_state.no_track_grace_until_ts = decision.set_no_track_grace_until_ts


def resolve_wait_timeout(
    *,
    last_player_state: Optional[str],
    poll_seconds: float,
    progress_update_seconds: float,
    toast_remaining_seconds: Optional[float],
) -> float:
    """Resolve loop wait timeout from explicit timing rules."""

    timeout = max(0.1, float(poll_seconds))
    player_state = str(last_player_state or "").strip().lower()
    if player_state == "playing":
        timeout = min(timeout, max(1.0, float(progress_update_seconds)))
    elif player_state in {"paused", "stopped", "unknown", "none", ""}:
        timeout = max(timeout, float(poll_seconds) * 2.0)
    if toast_remaining_seconds is not None:
        timeout = min(timeout, max(0.0, float(toast_remaining_seconds)))
    return timeout


def _safe_ms(v: object) -> Optional[int]:
    try:
        return max(0, int(v)) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def compute_display_elapsed_ms(state: LoopState, track: PlexTrack, now_ts: float) -> Optional[int]:
    """Estimate elapsed playback between coarse Plex metadata updates."""

    identity = _track_identity(track)
    if identity != state.last_track_identity:
        state.last_reported_elapsed_ms = None
        state.elapsed_anchor_ms = None
        state.elapsed_anchor_ts = now_ts
        state.last_track_identity = identity

    reported_ms = _safe_ms(track.elapsed_ms)
    duration_ms = _safe_ms(track.duration_ms)

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
