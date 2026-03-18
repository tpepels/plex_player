"""Shared constants for default configuration and app behavior."""

DEFAULT_PLEX_SERVER = "http://plex.local:32400"
DEFAULT_PLAYER_NAME = "Plexamp Pi Zero"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_FB_DEVICE = "/dev/fb1"

DEFAULT_WIDTH = 320
DEFAULT_HEIGHT = 240

DEFAULT_BUTTON_PLAY_PAUSE_PIN = 23
DEFAULT_BUTTON_STOP_PIN = 24
DEFAULT_BUTTON_NEXT_PIN = 25
DEFAULT_BUTTON_BOUNCE_TIME = 0.15

DEFAULT_BUTTON_LABEL_PLAY_Y_PERCENT = 20
DEFAULT_BUTTON_LABEL_STOP_Y_PERCENT = 40
DEFAULT_BUTTON_LABEL_NEXT_Y_PERCENT = 60

DEFAULT_POLL_SECONDS = 3
DEFAULT_WEATHER_REFRESH_SECONDS = 900
DEFAULT_PROGRESS_UPDATE_SECONDS = 3
DEFAULT_NO_TRACK_GRACE_SECONDS = 4.0
DEFAULT_DISPLAY_X_SHIFT = 0

DEFAULT_HTTP_TIMEOUT = 10
DEFAULT_COVER_RETRY_SECONDS = 20
DEFAULT_TOAST_DURATION_SECONDS = 0.7
DEFAULT_COMMAND_CONFIRM_SECONDS = 5.0

DEFAULT_FONT_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
DEFAULT_FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
DEFAULT_FONT_SYMBOLS_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}