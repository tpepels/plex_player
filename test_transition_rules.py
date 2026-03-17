import unittest

from models import LoopState, PlaybackSnapshot, PlexTrack, RuntimeState, TransitionDecision, TransitionMode
from transition_rules import (
    apply_button_rules,
    apply_transition_decision,
    compute_display_elapsed_ms,
    resolve_transition,
    resolve_wait_timeout,
    should_poll_timeline,
)


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
            command_id=42,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=5.0,
        )
        self.assertEqual(runtime.toast_text, "Stopped")
        self.assertEqual(runtime.toast_until_ts, 100.7)
        self.assertEqual(runtime.force_idle_until_ts, 104.0)
        pending = runtime.pending_command
        self.assertIsNotNone(pending)
        if pending is None:
            self.fail("pending_command must be set after stop")
        self.assertEqual(pending.action, "stop")
        self.assertEqual(pending.command_id, 42)

    def test_button_play_pause_clears_force_idle(self):
        runtime = RuntimeState(current_playback_state="playing", force_idle_until_ts=999.0)
        apply_button_rules(
            runtime,
            "play_pause",
            command_id=7,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=5.0,
        )
        self.assertEqual(runtime.toast_text, "Paused")
        self.assertEqual(runtime.force_idle_until_ts, 0.0)

    def test_transition_force_idle_has_priority(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing"),
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=15.0,
            force_idle_until_ts=20.0,
            pending_command=None,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.IDLE)
        self.assertEqual(decision.reason, "force_idle_window")

    def test_transition_playing_sets_grace_and_clears_force_idle(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing"),
            last_track_identity=None,
            last_player_state="paused",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=None,
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
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=12.0,
            force_idle_until_ts=0.0,
            pending_command=None,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.HOLD)
        self.assertEqual(decision.reason, "transient_no_track_gap")

    def test_pending_stop_confirmed_when_not_playing(self):
        runtime = RuntimeState(current_playback_state="playing")
        apply_button_rules(
            runtime,
            "stop",
            command_id=1,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=5.0,
        )
        snapshot = PlaybackSnapshot(
            now_ts=101.0,
            track=self._track(state="paused"),
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=runtime.pending_command,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.IDLE)
        self.assertEqual(decision.reason, "pending_stop_confirmed")
        self.assertTrue(decision.clear_pending_command)

    def test_pending_stop_confirmation_keeps_force_idle_window(self):
        runtime = RuntimeState(current_playback_state="playing")
        apply_button_rules(
            runtime,
            "stop",
            command_id=9,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=5.0,
        )
        snapshot = PlaybackSnapshot(
            now_ts=101.0,
            track=self._track(state="paused"),
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=104.0,
            pending_command=runtime.pending_command,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.reason, "pending_stop_confirmed")
        self.assertFalse(decision.clear_force_idle)
        self.assertIsNone(decision.idle_track)

    def test_pending_timeout_clears_command(self):
        runtime = RuntimeState(current_playback_state="playing")
        apply_button_rules(
            runtime,
            "next",
            command_id=2,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=1.0,
        )
        snapshot = PlaybackSnapshot(
            now_ts=102.0,
            track=None,
            last_track_identity=None,
            last_player_state="idle",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=runtime.pending_command,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertTrue(decision.clear_pending_command)

    def test_pending_next_confirmed_on_track_identity_change(self):
        runtime = RuntimeState(current_playback_state="playing")
        apply_button_rules(
            runtime,
            "next",
            command_id=3,
            now_ts=100.0,
            toast_duration_seconds=0.7,
            stop_force_idle_seconds=4.0,
            confirm_timeout_seconds=5.0,
        )
        snapshot = PlaybackSnapshot(
            now_ts=101.0,
            track=self._track(title="Song B", state="playing"),
            last_track_identity="Song A|Artist|Album|/thumb|100000",
            last_player_state="playing",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=runtime.pending_command,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.PLAYING)
        self.assertTrue(decision.clear_pending_command)

    def test_timeline_none_forces_idle_without_track_payload(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing"),
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=None,
            timeline_state="none",
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.IDLE)
        self.assertEqual(decision.reason, "timeline_not_playing")
        self.assertIsNone(decision.idle_track)

    def test_near_end_stale_playing_maps_to_idle_without_track(self):
        snapshot = PlaybackSnapshot(
            now_ts=10.0,
            track=self._track(state="playing", elapsed_ms=98_700, duration_ms=100_000),
            last_track_identity=None,
            last_player_state="playing",
            no_track_grace_until_ts=0.0,
            force_idle_until_ts=0.0,
            pending_command=None,
            timeline_state=None,
        )
        decision = resolve_transition(snapshot, no_track_grace_seconds=4.0)
        self.assertEqual(decision.mode, TransitionMode.IDLE)
        self.assertEqual(decision.reason, "default_idle")
        self.assertIsNone(decision.idle_track)

    def test_wait_timeout_uses_progress_when_playing(self):
        timeout = resolve_wait_timeout(
            last_player_state="playing",
            poll_seconds=5.0,
            progress_update_seconds=2.0,
            toast_remaining_seconds=None,
        )
        self.assertEqual(timeout, 2.0)

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

    def test_should_poll_timeline_only_when_recently_or_currently_playing(self):
        self.assertTrue(should_poll_timeline(self._track(state="playing"), "paused"))
        self.assertTrue(should_poll_timeline(None, "playing"))
        self.assertFalse(should_poll_timeline(self._track(state="paused"), "paused"))

    def test_apply_transition_decision_reducer_updates_states(self):
        runtime = RuntimeState(force_idle_until_ts=123.0, pending_command=None)
        state = LoopState(no_track_grace_until_ts=0.0)
        decision = TransitionDecision(
            mode=TransitionMode.PLAYING,
            reason="unit_test",
            set_no_track_grace_until_ts=14.0,
            clear_force_idle=True,
            clear_pending_command=False,
        )
        apply_transition_decision(runtime, state, decision)
        self.assertEqual(runtime.force_idle_until_ts, 0.0)
        self.assertEqual(state.no_track_grace_until_ts, 14.0)


if __name__ == "__main__":
    unittest.main()
