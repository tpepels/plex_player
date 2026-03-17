# Plex LCD Display

Lightweight Plex/Plexamp now-playing display for Raspberry Pi framebuffer screens.

## What It Does

- While music is playing: shows album art, track title, and artist.
- While idle: shows clock, location, weather icon/label, and temperature.
- When not playing: shows current playback status (for example `Paused` or `Stopped`).
- If artwork cannot be fetched: shows a "No Album Art" placeholder.
- Optional GPIO buttons can control Plexamp: play/pause, stop, next.
- When GPIO buttons are enabled, small on-screen labels are shown for them.

## Requirements

- Linux device with framebuffer display support (typically Raspberry Pi + `/dev/fb1`).
- Working LCD/framebuffer driver already installed.
- Plex server URL and a valid Plex token.
- Plex player name exactly as reported by active sessions.

## Install (From This Folder)

1. Make the setup script executable:

```bash
chmod +x setup_plexlcd.sh
```

1. Install dependencies:

```bash
./setup_plexlcd.sh install
```

1. Configure `.env`:

```bash
./setup_plexlcd.sh configure
```

1. Test Plex connectivity and player detection:

```bash
./setup_plexlcd.sh test
```

1. Run once manually to verify display output:

```bash
python3 plexlcd.py
```

1. Install/start as a service:

```bash
./setup_plexlcd.sh service
systemctl status plexlcd.service
```

## Configuration

Use [.env.example](.env.example) as template. The app and setup script use `.env` in this same directory.

Required keys:

- `PLEX_SERVER` (example: `http://your-plex-host:32400`)
- `PLEX_TOKEN`
- `PLAYER_NAME` (must match active Plex player title exactly)
- `LATITUDE`, `LONGITUDE`, `TIMEZONE`
- `FB_DEVICE` (usually `/dev/fb1`)
- `WIDTH`, `HEIGHT`

Optional GPIO button keys:

- `BUTTONS_ENABLED`
- `BUTTON_PLAY_PAUSE_PIN` (default `23`)
- `BUTTON_STOP_PIN` (default `24`)
- `BUTTON_NEXT_PIN` (default `25`)
- `BUTTON_BOUNCE_TIME` (default `0.15`)
- `BUTTON_LABEL_PLAY_Y_PERCENT` (default `20`)
- `BUTTON_LABEL_STOP_Y_PERCENT` (default `40`)
- `BUTTON_LABEL_NEXT_Y_PERCENT` (default `60`)

Other useful optional keys:

- `LOCATION_NAME` (override location label on idle screen)
- `DISPLAY_X_SHIFT` (horizontal framebuffer correction)
- `DEBUG_LOGGING` (`1` enables detailed loop/button debug logs)
- `FONT_PATH_REGULAR`, `FONT_PATH_BOLD`, `FONT_PATH_SYMBOLS` (override font paths)

## Expected Output

- `./setup_plexlcd.sh test` should report successful Plex connectivity.
- During playback, screen should switch to album art view.
- When playback stops/pauses, screen should return to idle clock/weather and show state text.

## Troubleshooting

- `Unauthorized` from `/status/sessions`:
  - Token is invalid/expired, or server URL is wrong.
  - Refresh token from Plex Web network requests and update `.env`.

- `No Album Art` while playback is active:
  - Check `PLAYER_NAME` in `.env` matches the active session exactly.
  - Re-run `./setup_plexlcd.sh test` during playback.

- Unicode symbols or international text render as boxes:
  - Run `./setup_plexlcd.sh install` to ensure Noto fonts are installed.
  - If needed, override font paths in `.env` (`FONT_PATH_*`).

- `Permission denied` writing framebuffer:
  - Ensure service is running as configured by setup script.
  - Confirm `FB_DEVICE` exists and is writable.

- Screen shows nothing:
  - Verify LCD driver and framebuffer (`./setup_plexlcd.sh fb`).
  - Test framebuffer with `fbi`.
