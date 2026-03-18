import unittest

from core import LoopState, PlaybackCollectorConfig, PlaybackCollectorDeps, PlexTrack, RuntimeState, collect_playback_snapshot


class PlaybackCollectorTests(unittest.TestCase):
    def _track(self, **kwargs) -> PlexTrack:
        data = {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "thumb_path": "/thumb",
            "state": "playing",
            "target_client_identifier": "client-1",
            "player_address": "192.168.1.10",
            "player_port": 32500,
        }
        data.update(kwargs)
        return PlexTrack(**data)

    def test_collect_updates_runtime_and_snapshot(self):
        runtime = RuntimeState(force_idle_until_ts=2.0)
        loop_state = LoopState(last_player_state="paused", no_track_grace_until_ts=5.0)
        track = self._track()

        collected = collect_playback_snapshot(
            now_ts=10.0,
            loop_state=loop_state,
            runtime_state=runtime,
            config=PlaybackCollectorConfig("Player", "http://plex.local:32400", "token", 10),
            deps=PlaybackCollectorDeps(
                fetch_sessions_json=lambda **kwargs: {"ok": True},
                find_player_track=lambda data, player_name: track,
                fetch_player_timeline_state=lambda **kwargs: {"state": "paused"},
                should_poll_timeline=lambda track, last_player_state: True,
                log_warn=lambda msg: None,
                log_error=lambda msg: None,
            ),
        )

        self.assertIs(collected.track, track)
        self.assertEqual(runtime.current_target_client_id, "client-1")
        self.assertEqual(runtime.current_player_address, "192.168.1.10")
        self.assertEqual(collected.snapshot.track, track)
        self.assertEqual(collected.snapshot.timeline_state, "paused")
        self.assertEqual(collected.snapshot.force_idle_until_ts, 2.0)

    def test_collect_skips_timeline_when_policy_says_no(self):
        runtime = RuntimeState(current_player_address="192.168.1.20", current_player_port=32500)
        loop_state = LoopState(last_player_state="paused")
        timeline_called = False

        def fetch_timeline(**kwargs):
            nonlocal timeline_called
            timeline_called = True
            return {"state": "playing"}

        collected = collect_playback_snapshot(
            now_ts=10.0,
            loop_state=loop_state,
            runtime_state=runtime,
            config=PlaybackCollectorConfig("Player", "http://plex.local:32400", "token", 10),
            deps=PlaybackCollectorDeps(
                fetch_sessions_json=lambda **kwargs: None,
                find_player_track=lambda data, player_name: None,
                fetch_player_timeline_state=fetch_timeline,
                should_poll_timeline=lambda track, last_player_state: False,
                log_warn=lambda msg: None,
                log_error=lambda msg: None,
            ),
        )

        self.assertFalse(timeline_called)
        self.assertIsNone(collected.track)
        self.assertIsNone(collected.snapshot.timeline_state)
        self.assertEqual(runtime.current_playback_state, "unknown")


if __name__ == "__main__":
    unittest.main()