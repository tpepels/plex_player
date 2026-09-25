import unittest
from unittest.mock import patch

import plexlcd
from core import LoopState, WeatherInfo


class RuntimeHelperTests(unittest.TestCase):
    def test_power_save_grace_outlasts_effective_poll_interval(self):
        with (
            patch.object(plexlcd, "POLL_SECONDS", 3.0),
            patch.object(plexlcd, "WEATHER_REFRESH_SECONDS", 900.0),
            patch.object(plexlcd, "PROGRESS_UPDATE_SECONDS", 3.0),
            patch.object(plexlcd, "TIMELINE_POLL_MIN_INTERVAL_SECONDS", 8.0),
            patch.object(plexlcd, "COVER_RETRY_SECONDS", 20.0),
            patch.object(plexlcd, "NO_TRACK_GRACE_SECONDS", 4.0),
            patch.object(plexlcd, "LOW_POWER_COVER_RENDER", False),
        ):
            plexlcd.apply_power_save_tunings()
            self.assertGreater(plexlcd.NO_TRACK_GRACE_SECONDS, plexlcd.POLL_SECONDS)

    def test_weather_fetches_immediately_even_just_after_boot(self):
        state = LoopState()
        weather = WeatherInfo(temp_c=20.0, weather_code=0, is_day=1)

        with (
            patch.object(plexlcd, "WEATHER_REFRESH_SECONDS", 1800.0),
            patch.object(plexlcd, "fetch_weather", return_value=weather) as fetch,
        ):
            plexlcd.refresh_weather_if_due(state, now_ts=5.0)

        fetch.assert_called_once()
        self.assertIs(state.last_weather, weather)
        self.assertEqual(state.last_weather_fetch, 5.0)

    def test_failed_weather_refresh_keeps_cache_and_retries_soon(self):
        cached = WeatherInfo(temp_c=19.0, weather_code=2, is_day=1)
        state = LoopState(last_weather=cached, last_weather_fetch=0.0)

        with (
            patch.object(plexlcd, "WEATHER_REFRESH_SECONDS", 1800.0),
            patch.object(plexlcd, "fetch_weather", return_value=None) as fetch,
        ):
            plexlcd.refresh_weather_if_due(state, now_ts=2000.0)
            plexlcd.refresh_weather_if_due(state, now_ts=2020.0)

        fetch.assert_called_once()
        self.assertIs(state.last_weather, cached)
        self.assertEqual(state.next_weather_retry_ts, 2060.0)


if __name__ == "__main__":
    unittest.main()
