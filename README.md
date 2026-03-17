# Plex LCD Display

Lightweight Plex/Plexamp now-playing display for Raspberry Pi framebuffer screens.

## What It Does

- While music is playing: shows album art, track title, and artist.
- While idle: shows clock and weather.
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

2. Install dependencies:

```bash
./setup_plexlcd.sh install
```

3. Configure `.env`:

```bash
./setup_plexlcd.sh configure
```

4. Test Plex connectivity and player detection:

```bash
./setup_plexlcd.sh test
```

5. Run once manually to verify display output:

```bash
python3 plexlcd.py
```

6. Install/start as a service:

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

## Expected Output

- `./setup_plexlcd.sh test` should report successful Plex connectivity.
- During playback, screen should switch to album art view.
- When playback stops/pauses, screen should return to idle clock/weather.

## Troubleshooting

- `Unauthorized` from `/status/sessions`:
	- Token is invalid/expired, or server URL is wrong.
	- Refresh token from Plex Web network requests and update `.env`.

- `No Album Art` while playback is active:
	- Check `PLAYER_NAME` in `.env` matches the active session exactly.
	- Re-run `./setup_plexlcd.sh test` during playback.

- `Permission denied` writing framebuffer:
	- Ensure service is running as configured by setup script.
	- Confirm `FB_DEVICE` exists and is writable.

- Screen shows nothing:
	- Verify LCD driver and framebuffer (`./setup_plexlcd.sh fb`).
	- Test framebuffer with `fbi`.