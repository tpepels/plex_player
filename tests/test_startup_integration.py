import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class StartupIntegrationTests(unittest.TestCase):
    def test_invalid_config_exits_with_validation_error(self):
        repo_root = Path(__file__).resolve().parents[1]
        app_path = repo_root / "plexlcd.py"

        with tempfile.TemporaryDirectory() as td:
            fb_path = Path(td) / "fb.bin"
            fb_path.write_bytes(b"")

            env_file = Path(td) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "PLEX_SERVER=http://example:32400",
                        "PLEX_TOKEN=",
                        "PLAYER_NAME=Player",
                        "LATITUDE=0.0",
                        "LONGITUDE=0.0",
                        "TIMEZONE=UTC",
                        f"FB_DEVICE={fb_path}",
                        "WIDTH=320",
                        "HEIGHT=240",
                        "BUTTONS_ENABLED=0",
                        "POLL_SECONDS=3",
                        "WEATHER_REFRESH_SECONDS=900",
                        "PROGRESS_UPDATE_SECONDS=5",
                        "NO_TRACK_GRACE_SECONDS=4.0",
                        "DISPLAY_X_SHIFT=0",
                    ]
                ),
                encoding="utf-8",
            )

            subproc_env = dict(os.environ)
            for key in (
                "PLEX_SERVER",
                "PLEX_TOKEN",
                "PLAYER_NAME",
                "LATITUDE",
                "LONGITUDE",
                "TIMEZONE",
                "FB_DEVICE",
                "WIDTH",
                "HEIGHT",
                "BUTTONS_ENABLED",
                "POLL_SECONDS",
                "WEATHER_REFRESH_SECONDS",
                "PROGRESS_UPDATE_SECONDS",
                "NO_TRACK_GRACE_SECONDS",
                "DISPLAY_X_SHIFT",
            ):
                subproc_env.pop(key, None)

            proc = subprocess.run(
                [sys.executable, str(app_path)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=20,
                env={
                    **subproc_env,
                    "PLEXLCD_ENV": str(env_file),
                    "PLEXLCD_STARTUP_TRACE": "0",
                    "PLEXLCD_STARTUP_LOG": "",
                },
            )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("[startup] [ERROR] Configuration errors:", proc.stderr)
        self.assertIn("[startup] [ERROR] - PLEX_TOKEN not set or empty", proc.stderr)


if __name__ == "__main__":
    unittest.main()
