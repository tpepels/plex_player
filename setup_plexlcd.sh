#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/plexlcd}"
ENV_FILE="$APP_DIR/.env"
SERVICE_FILE="/etc/systemd/system/plexlcd.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

require_root_for_apt() {
  if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
  else
    SUDO=""
  fi
}

install_packages() {
  require_root_for_apt
  log "Installing packages"
  $SUDO apt-get update
  $SUDO apt-get install -y \
    python3 python3-pip python3-venv python3-requests python3-pil \
    curl jq fonts-dejavu-core fbset fbi
}

copy_python_script() {
  mkdir -p "$APP_DIR"
  cp "$(dirname "$0")/plexlcd.py" "$APP_DIR/plexlcd.py"
  chmod +x "$APP_DIR/plexlcd.py"
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "$prompt [$default]: " value || true
  if [[ -z "${value:-}" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "$value"
  fi
}

prompt_secret() {
  local prompt="$1"
  local value
  read -r -s -p "$prompt: " value || true
  printf '\n' >&2
  printf '%s' "$value"
}

write_env() {
  mkdir -p "$APP_DIR"

  local plex_host player_name plex_token latitude longitude timezone fb_device width height poll_seconds weather_refresh
  plex_host=$(prompt_default "Plex server URL" "http://192.168.1.200:32400")
  player_name=$(prompt_default "Exact Plexamp player name" "Plexamp Pi Zero")
  plex_token=$(prompt_secret "Plex token")
  latitude=$(prompt_default "Latitude" "41.1579")
  longitude=$(prompt_default "Longitude" "-8.6291")
  timezone=$(prompt_default "Timezone" "Europe/Lisbon")
  fb_device=$(prompt_default "Framebuffer device" "/dev/fb1")
  width=$(prompt_default "Display width" "320")
  height=$(prompt_default "Display height" "240")
  poll_seconds=$(prompt_default "Plex poll interval seconds" "3")
  weather_refresh=$(prompt_default "Weather refresh seconds" "900")

  cat > "$ENV_FILE" <<EOFENV
PLEX_SERVER=$plex_host
PLEX_TOKEN=$plex_token
PLAYER_NAME=$player_name
LATITUDE=$latitude
LONGITUDE=$longitude
TIMEZONE=$timezone
FB_DEVICE=$fb_device
WIDTH=$width
HEIGHT=$height
POLL_SECONDS=$poll_seconds
WEATHER_REFRESH_SECONDS=$weather_refresh
EOFENV

  chmod 600 "$ENV_FILE"
  log "Wrote $ENV_FILE"
}

test_plex() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log "No .env file yet, skipping Plex test"
    return 0
  fi

  # shellcheck disable=SC1090
  source "$ENV_FILE"
  log "Testing Plex connectivity"

  local url
  url="$PLEX_SERVER/status/sessions?X-Plex-Token=$PLEX_TOKEN"
  if ! curl -fsS "$url" >/tmp/plex_sessions_test.json 2>/tmp/plex_sessions_test.err; then
    printf 'Plex test failed. Error:\n' >&2
    cat /tmp/plex_sessions_test.err >&2 || true
    return 1
  fi

  log "Plex sessions endpoint responded successfully"
  printf 'Players seen in current sessions:\n'
  jq -r '.MediaContainer.Metadata // [] | (if type == "array" then . else [.] end)[] | [.Player.title, .Player.device, .Player.state, .title, .grandparentTitle] | @tsv' /tmp/plex_sessions_test.json \
    | awk -F '\t' '{printf("- player=%s | device=%s | state=%s | track=%s | artist=%s\n", $1, $2, $3, $4, $5)}' || true
}

show_framebuffers() {
  log "Detecting framebuffer devices"
  if compgen -G "/dev/fb*" > /dev/null; then
    ls -l /dev/fb*
    printf '\nfbset output:\n'
    fbset -s || true
  else
    printf 'No /dev/fb* devices found. The screen driver may not be installed yet.\n'
  fi
}

show_token_help() {
  cat <<'EOHELP'
How to get your Plex token:
  1. Open http://192.168.1.200:32400/web in a browser.
  2. Open browser dev tools -> Network.
  3. Reload Plex.
  4. Click a request to /status/sessions, /library, or /metadata.
  5. Look for X-Plex-Token=... in the request URL or headers.

How to get the exact player name:
  1. Start playback on the Pi's Plexamp.
  2. Run this script after entering your token.
  3. It will list players found in /status/sessions.
  4. Copy the exact player title into PLAYER_NAME.
EOHELP
}

install_service() {
  require_root_for_apt
  if [[ ! -f "$ENV_FILE" ]]; then
    log "No .env file found, cannot install service yet"
    return 1
  fi

  log "Installing systemd service"
  log "Note: Service will run as root to access framebuffer device"
  cat <<EOFUNIT | $SUDO tee "$SERVICE_FILE" >/dev/null
[Unit]
Description=Plex LCD now-playing display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $APP_DIR/plexlcd.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOFUNIT

  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now plexlcd.service
  $SUDO systemctl status --no-pager plexlcd.service || true
}

usage() {
  cat <<EOFUSAGE
Usage: $(basename "$0") [command]

Commands:
  help            Show token/player-name instructions
  install         Install packages and copy plexlcd.py to $APP_DIR
  configure       Create or update $ENV_FILE interactively
  test            Test Plex connectivity and list players from current sessions
  fb              Show framebuffer devices
  service         Install and start systemd service
  all             Run install, configure, test, fb, and service
EOFUSAGE
}

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    help) show_token_help ;;
    install) install_packages; copy_python_script ;;
    configure) write_env ;;
    test) test_plex ;;
    fb) show_framebuffers ;;
    service) install_service ;;
    all)
      install_packages
      copy_python_script
      write_env
      test_plex
      show_framebuffers
      install_service
      ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"