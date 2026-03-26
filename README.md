# Plex LCD Display

A small Raspberry Pi display app for Plex/Plexamp.

When music is playing, it shows album art and track info.
When nothing is playing, it shows a clock and weather.

## Quick Start

Run these commands from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x setup_plexlcd.sh
./setup_plexlcd.sh install
./setup_plexlcd.sh configure
./setup_plexlcd.sh test
.venv/bin/python plexlcd.py
```

If everything looks good, install it as a service:

```bash
./setup_plexlcd.sh service
systemctl status plexlcd.service
```

## What You Get

- Playing: album art, title, artist, progress, elapsed/total time
- Idle: clock, location label, weather symbol, temperature
- Not playing: status text (for example `Paused`)
- Optional GPIO controls: play/pause, stop, next

## What You Need

- Linux device with framebuffer support (common on Raspberry Pi)
- A working display device (usually `/dev/fb1`)
- Plex server URL
- Plex token
- Plex player name exactly as shown in Plex sessions

## Screen Compatibility

This project is being developed and tuned for a specific 320x240 framebuffer LCD setup on Raspberry Pi.

If you use a different screen, you should expect to adjust layout and device settings.

Start with these `.env` values:

- `FB_DEVICE`: framebuffer path for your panel (common values: `/dev/fb0`, `/dev/fb1`)
- `WIDTH`, `HEIGHT`: your real panel resolution
- `DISPLAY_X_SHIFT`: horizontal offset correction if the image is shifted

Then tune visual/button placement as needed:

- `BUTTON_LABEL_PLAY_Y_PERCENT`
- `BUTTON_LABEL_STOP_Y_PERCENT`
- `BUTTON_LABEL_NEXT_Y_PERCENT`

Recommended adaptation flow:

1. Set framebuffer and resolution values.
2. Run `./setup_plexlcd.sh fb` to verify the panel and framebuffer mapping.
3. Run `python3 plexlcd.py` and check alignment/cropping.
4. Adjust `DISPLAY_X_SHIFT` and button label percentages until it looks right.
5. If text/symbols render poorly, install fonts with `./setup_plexlcd.sh install` and optionally set `FONT_PATH_*` values.

## Configuration

The app reads `.env` in this folder.
Start from [.env.example](.env.example) if needed.

Most important values:

- `PLEX_SERVER` (example: `http://192.168.1.200:32400`)
- `PLEX_TOKEN`
- `PLAYER_NAME`
- `LATITUDE`, `LONGITUDE`, `TIMEZONE`
- `FB_DEVICE`, `WIDTH`, `HEIGHT`

Useful optional values:

- `LOCATION_NAME` (custom text under the clock)
- `DISPLAY_X_SHIFT` (panel alignment tweak)
- `BUTTONS_ENABLED` and button pin values
- `DEBUG_LOGGING=1` (more logs)
- `PROGRESS_UPDATE_SECONDS` (higher value lowers CPU while playing; try `8` to `12` on Pi Zero-class devices)
- `TIMELINE_POLL_MIN_INTERVAL_SECONDS` (minimum seconds between direct timeline polls while playing, default `8`)
- `LOW_POWER_COVER_RENDER=1` (use cheaper cover-art scaling to reduce CPU on album page)

## Daily Commands

```bash
make test
make lint
make run
```

`make` targets use `.venv/bin/python` by default.

## Troubleshooting

`./setup_plexlcd.sh test` fails with Unauthorized:
- Check `PLEX_SERVER`
- Replace `PLEX_TOKEN` with a valid token

App runs but says no album art:
- Make sure `PLAYER_NAME` exactly matches the active Plex player session name

Permission denied writing framebuffer:
- Check `FB_DEVICE`
- Run through the service setup so permissions are configured correctly

Screen stays blank:
- Confirm the framebuffer works first: `./setup_plexlcd.sh fb`

## For Developers

Project layout:

- `plexlcd.py`: entrypoint
- `core/`: rules, models, config, rendering policies, adapters
- `services/`: Plex and weather API integrations
- `tests/`: unit tests

CI:

- GitHub Actions workflow: `.github/workflows/ci.yml`
- Runs `make test` and `make lint` on push and pull request
