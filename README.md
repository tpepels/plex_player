# Plex LCD Display

Show Plex/Plexamp album art on a 320x240 framebuffer display. When nothing is playing, show clock + weather.

Works from any current directory. The script always uses files in its own directory.

## Quick Start

1. Make script executable:

```bash
SCRIPT="/absolute/path/to/setup_plexlcd.sh"
chmod +x "$SCRIPT"
```

2. Install dependencies:

```bash
"$SCRIPT" install
```

3. Configure environment:

```bash
"$SCRIPT" configure
```

4. Test Plex connection and player detection:

```bash
"$SCRIPT" test
```

5. Install and start service:

```bash
"$SCRIPT" service
systemctl status plexlcd.service
```

## Required .env Settings

Use [.env.example](.env.example) as a template.

- PLEX_SERVER: Example http://your-plex-host:32400
- PLEX_TOKEN: Plex API token
- PLAYER_NAME: Exact player title from active sessions
- LATITUDE, LONGITUDE, TIMEZONE: Weather and local time
- FB_DEVICE: Usually /dev/fb1
- WIDTH, HEIGHT: Display resolution

The setup script and service use `.env` from the same directory as `setup_plexlcd.sh`.

## Useful Commands

```bash
"$SCRIPT" fb
"$SCRIPT" help
python3 "$(dirname "$SCRIPT")/plexlcd.py"
```

## Notes

- Service runs as root to ensure framebuffer write access.
- If your display is rotated, use LCD-show rotate script (0, 90, 180, 270).