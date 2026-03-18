import unittest

from core import (
    LoopState,
    PlexTrack,
    commit_idle_render_state,
    commit_playing_render_state,
    resolve_idle_render_plan,
    resolve_playing_render_plan,
)


class RenderPolicyTests(unittest.TestCase):
    def _track(self, **kwargs) -> PlexTrack:
        data = {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "thumb_path": "/thumb",
            "state": "playing",
            "target_client_identifier": "client-1",
            "elapsed_ms": 10_000,
            "duration_ms": 100_000,
        }
        data.update(kwargs)
        return PlexTrack(**data)

    def test_playing_plan_detects_progress_only_redraw(self):
        state = LoopState(
            last_thumb_path="/thumb",
            last_track_title="Song",
            last_track_identity="Song|Artist|Album|/thumb|100000",
            last_player_state="playing",
            last_elapsed_second=3,
            elapsed_anchor_ms=10_000,
            elapsed_anchor_ts=100.0,
            last_reported_elapsed_ms=10_000,
            last_toast_visible=False,
            next_cover_retry_ts=999.0,
        )
        plan = resolve_playing_render_plan(state, self._track(), 104.0, progress_update_seconds=1, toast_visible=False)
        self.assertTrue(plan.progress_changed)
        self.assertFalse(plan.needs_refresh)
        self.assertFalse(plan.needs_retry)
        self.assertTrue(plan.should_render)

    def test_idle_plan_skips_unchanged_idle_frame(self):
        state = LoopState(last_idle_minute="2026-03-18 12:34", last_player_state="paused", last_toast_visible=True)
        plan = resolve_idle_render_plan(state, "2026-03-18 12:34", self._track(state="paused"), "Paused", True)
        self.assertFalse(plan.should_render)

    def test_commit_helpers_update_loop_cache(self):
        state = LoopState()
        playing_plan = resolve_playing_render_plan(state, self._track(), 100.0, progress_update_seconds=3, toast_visible=True)
        commit_playing_render_state(state, self._track(), playing_plan, next_retry_ts=123.0)
        self.assertEqual(state.last_player_state, "playing")
        self.assertEqual(state.last_thumb_path, "/thumb")
        self.assertEqual(state.next_cover_retry_ts, 123.0)

        idle_plan = resolve_idle_render_plan(state, "2026-03-18 12:35", None, None, False)
        commit_idle_render_state(state, idle_plan)
        self.assertEqual(state.last_idle_minute, "2026-03-18 12:35")
        self.assertEqual(state.last_player_state, "unknown")
        self.assertIsNone(state.cached_cover)


if __name__ == "__main__":
    unittest.main()