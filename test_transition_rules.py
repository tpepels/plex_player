import unittest

from models import LoopState, PlaybackSnapshot, PlexTrack, RuntimeState, TransitionMode
from transition_rules import apply_button_rules, compute_display_elapsed_ms, resolve_transition


class TransitionRulesTests(unittest.TestCase):
    def _track(self, **kwargs):
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

    def test_button_stop_sets_force_idle_and_toast(self):
        runtime = RuntimeState(current_playback_state="playing")
        apply_button_rules(
            runtime,
            "stop",
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
        )
        self.assertEqual(runtime.toast_text, "Stopped")
        self.assertEqual(runtime.toast_until_ts, 100.7)
        self.assertEqual(runtime.force_idle_until_ts, 104.0)

    def test_button_play_pause_clears_force_idle(self):
        runtime = RuntimeState(current_playback_state="playing", force_idle_until_ts=999.0)
        apply_button_rules(
            runtime,
            "play_pause",
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
        )
        self.assertEqual(runtime.toast_text, "Paused")
        self.assertEqual(runtime.force_idle_until_ts, 0.0)

    def test_transition_force_idle_has_priority(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing"),
            last_player_state="playing",
            no_track_grace_until_ts=15.0,
            force_idle_until_ts=20.0,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.IDLE)
        self.assertEqual(decision.reason, "force_idle_window")

    def test_transition_playing_sets_grace_and_clears_force_idle(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing"),
            last_player_state="paused",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.PLAYING)
        self.assertEqual(decision.reason, "plex_playing")
        self.assertEqual(decision.set_no_track_grace_until_ts, 14.0)
        self.assertTrue(decision.clear_force_idle)

    def test_transition_hold_when_transient_no_track(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=None,
            last_player_state="playing",
            no_track_grace_until_ts=12.0,
            force_idle_until_ts=0.0,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.HOLD)
        self.assertEqual(decision.reason, "transient_no_track_gap")

    def test_elapsed_interpolation_and_clamp(self):
        state = LoopState()
        track = self._track(elapsed_ms=5_000, duration_ms=6_000)
        first = compute_display_elapsed_ms(state, track, now_ts=100.0)
        later = compute_display_elapsed_ms(state, track, now_ts=102.0)
        self.assertEqual(first, 5_000)
        self.assertEqual(later, 6_000)

    def test_elapsed_resets_on_identity_change(self):
        state = LoopState()
        t1 = self._track(title="Song A", elapsed_ms=10_000)
        t2 = self._track(title="Song B", elapsed_ms=1_000)
        _ = compute_display_elapsed_ms(state, t1, now_ts=100.0)
        value = compute_display_elapsed_ms(state, t2, now_ts=101.0)
        self.assertEqual(value, 1_000)


if __name__ == "__main__":
    unittest.main()
