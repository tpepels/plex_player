import os
import tempfile
import unittest
from unittest.mock import patch

from core.config import Config


class ConfigTests(unittest.TestCase):
    def test_buttons_enabled_without_gpiozero_is_not_fatal(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            fb_path = tmp.name

        env = {
            "PLEX_SERVER": "http://example:32400",
            "PLEX_TOKEN": "token",
            "PLAYER_NAME": "Player",
            "LATITUDE": "0.0",
            "LONGITUDE": "0.0",
            "TIMEZONE": "UTC",
            "FB_DEVICE": fb_path,
            "WIDTH": "320",
            "HEIGHT": "240",
            "BUTTONS_ENABLED": "1",
            "POLL_SECONDS": "3",
            "WEATHER_REFRESH_SECONDS": "900",
            "PROGRESS_UPDATE_SECONDS": "5",
            "NO_TRACK_GRACE_SECONDS": "4.0",
            "DISPLAY_X_SHIFT": "0",
        }

        try:
            with patch.dict(os.environ, env, clear=False):
                cfg, errors = Config.from_env(button_available=False)

            self.assertTrue(cfg.buttons_enabled)
            self.assertEqual(errors, [])
        finally:
            if os.path.exists(fb_path):
                os.unlink(fb_path)


if __name__ == "__main__":
    unittest.main()
