"""Pure render-policy helpers for redraw decisions and cache state commits."""

from dataclasses import dataclass
from typing import Optional

from core.models import LoopState, PlexTrack
from core.transition_rules import compute_display_elapsed_ms


@dataclass(frozen=True)
class PlayingRenderPlan:
    display_elapsed_ms: Optional[int]
    progress_bucket: Optional[int]
    toast_visible: bool
    thumb_changed: bool
    progress_changed: bool
    toast_changed: bool
    needs_refresh: bool
    needs_retry: bool

    @property
    def should_render(self) -> bool:
        return self.needs_refresh or self.needs_retry or self.progress_changed or self.toast_changed


@dataclass(frozen=True)
class IdleRenderPlan:
    minute_key: str
    idle_state: str
    status_text: Optional[str]
    toast_visible: bool

    @property
    def should_render(self) -> bool:
        return not (
            self.minute_key == self._last_idle_minute
            and self.idle_state == self._last_player_state
            and self.toast_visible == self._last_toast_visible
        )

    _last_idle_minute: Optional[str]
    _last_player_state: Optional[str]
    _last_toast_visible: Optional[bool]


def resolve_playing_render_plan(
    state: LoopState,
    track: PlexTrack,
    now_ts: float,
    progress_update_seconds: int,
    toast_visible: bool,
) -> PlayingRenderPlan:
    display_elapsed_ms = compute_display_elapsed_ms(state, track, now_ts)
    elapsed_second = (display_elapsed_ms // 1000) if display_elapsed_ms is not None else None
    progress_bucket = (
        elapsed_second // progress_update_seconds
        if elapsed_second is not None and progress_update_seconds > 0
        else None
    )
    thumb_changed = track.thumb_path != state.last_thumb_path
    progress_changed = progress_bucket != state.last_elapsed_second
    toast_changed = toast_visible != state.last_toast_visible
    needs_refresh = thumb_changed or track.title != state.last_track_title or state.last_player_state != "playing"
    needs_retry = not state.cached_cover and not thumb_changed and now_ts >= state.next_cover_retry_ts
    return PlayingRenderPlan(
        display_elapsed_ms=display_elapsed_ms,
        progress_bucket=progress_bucket,
        toast_visible=toast_visible,
        thumb_changed=thumb_changed,
        progress_changed=progress_changed,
        toast_changed=toast_changed,
        needs_refresh=needs_refresh,
        needs_retry=needs_retry,
    )


def commit_playing_render_state(state: LoopState, track: PlexTrack, plan: PlayingRenderPlan, *, next_retry_ts: float) -> None:
    state.last_thumb_path = track.thumb_path
    state.last_track_title = track.title
    state.last_player_state = "playing"
    state.last_elapsed_second = plan.progress_bucket
    state.last_toast_visible = plan.toast_visible
    state.next_cover_retry_ts = next_retry_ts


def resolve_idle_render_plan(
    state: LoopState,
    minute_key: str,
    track: Optional[PlexTrack],
    status_text: Optional[str],
    toast_visible: bool,
) -> IdleRenderPlan:
    return IdleRenderPlan(
        minute_key=minute_key,
        idle_state=track.state if track else "unknown",
        status_text=status_text,
        toast_visible=toast_visible,
        _last_idle_minute=state.last_idle_minute,
        _last_player_state=state.last_player_state,
        _last_toast_visible=state.last_toast_visible,
    )


def commit_idle_render_state(state: LoopState, plan: IdleRenderPlan) -> None:
    state.last_idle_minute = plan.minute_key
    state.last_player_state = plan.idle_state
    state.last_thumb_path = None
    state.last_track_title = None
    state.last_track_identity = None
    state.last_elapsed_second = None
    state.last_reported_elapsed_ms = None
    state.elapsed_anchor_ms = None
    state.elapsed_anchor_ts = 0.0
    state.last_toast_visible = plan.toast_visible
    state.cached_cover = None
    state.next_cover_retry_ts = 0.0