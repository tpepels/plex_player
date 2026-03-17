#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
ENV_FILE="$SCRIPT_DIR/.env"
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
  if ! command -v apt-get >/dev/null 2>&1; then
    log "apt-get not found. Install dependencies manually for your distro."
    return 1
  fi
  log "Installing packages"
  $SUDO apt-get update
  $SUDO apt-get install -y \
    python3 python3-pip python3-venv python3-requests python3-pil python3-gpiozero \
    curl jq fonts-dejavu-core fbset fbi
}

prepare_local_files() {
  if [[ ! -f "$SCRIPT_DIR/plexlcd.py" ]]; then
    log "Missing $SCRIPT_DIR/plexlcd.py"
    return 1
  fi
  chmod +x "$SCRIPT_DIR/plexlcd.py" || true
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

load_env_file() {
  local file="$1"
  local line key value

  if [[ ! -f "$file" ]]; then
    return 1
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"

    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      continue
    fi

    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    export "$key=$value"
  done < "$file"
}

write_env() {
  mkdir -p "$APP_DIR"

  local plex_host player_name plex_token latitude longitude timezone fb_device width height poll_seconds weather_refresh display_x_shift buttons_enabled button_play_pause_pin button_stop_pin button_next_pin
  plex_host=$(prompt_default "Plex server URL" "http://plex.local:32400")
  player_name=$(prompt_default "Exact Plexamp player name" "Plexamp Pi Zero")
  plex_token=$(prompt_secret "Plex token")
  latitude=$(prompt_default "Latitude" "0.0000")
  longitude=$(prompt_default "Longitude" "0.0000")
  timezone=$(prompt_default "Timezone" "UTC")
  fb_device=$(prompt_default "Framebuffer device" "/dev/fb1")
  width=$(prompt_default "Display width" "320")
  height=$(prompt_default "Display height" "240")
  display_x_shift=$(prompt_default "Display X shift (pixels, negative/positive)" "0")
  buttons_enabled=$(prompt_default "Enable GPIO buttons (0/1)" "1")
  button_play_pause_pin=$(prompt_default "Play/Pause button GPIO pin" "23")
  button_stop_pin=$(prompt_default "Stop button GPIO pin" "24")
  button_next_pin=$(prompt_default "Next button GPIO pin" "25")
  poll_seconds=$(prompt_default "Plex poll interval seconds" "3")
  weather_refresh=$(prompt_default "Weather refresh seconds" "900")

  cat > "$ENV_FILE" <<EOFENV
PLEX_SERVER="$plex_host"
PLEX_TOKEN="$plex_token"
PLAYER_NAME="$player_name"
LATITUDE="$latitude"
LONGITUDE="$longitude"
TIMEZONE="$timezone"
FB_DEVICE="$fb_device"
WIDTH="$width"
HEIGHT="$height"
DISPLAY_X_SHIFT="$display_x_shift"
BUTTONS_ENABLED="$buttons_enabled"
BUTTON_PLAY_PAUSE_PIN="$button_play_pause_pin"
BUTTON_STOP_PIN="$button_stop_pin"
BUTTON_NEXT_PIN="$button_next_pin"
POLL_SECONDS="$poll_seconds"
WEATHER_REFRESH_SECONDS="$weather_refresh"
EOFENV

  chmod 600 "$ENV_FILE"
  log "Wrote $ENV_FILE"
}

test_plex() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log "No .env file found at $ENV_FILE, run: $(basename "$0") configure"
    return 1
  fi

  load_env_file "$ENV_FILE"
  if [[ -z "${PLEX_SERVER:-}" || -z "${PLEX_TOKEN:-}" ]]; then
    log "PLEX_SERVER or PLEX_TOKEN missing in $ENV_FILE"
    return 1
  fi
  log "Testing Plex connectivity"

  local url sessions_tmp err_tmp
  sessions_tmp="$(mktemp /tmp/plex_sessions_test.XXXXXX.json)"
  err_tmp="$(mktemp /tmp/plex_sessions_test.XXXXXX.err)"

  url="$PLEX_SERVER/status/sessions"
  if ! curl -fsS \
    -H "Accept: application/json" \
    -H "X-Plex-Token: $PLEX_TOKEN" \
    "$url" >"$sessions_tmp" 2>"$err_tmp"; then
    printf 'Plex test failed. Error:\n' >&2
    cat "$err_tmp" >&2 || true
    rm -f "$sessions_tmp" "$err_tmp"
    return 1
  fi

  log "Plex sessions endpoint responded successfully"
  if command -v jq >/dev/null 2>&1; then
    if ! jq -e . >/dev/null 2>&1 <"$sessions_tmp"; then
      printf 'Plex responded, but not in JSON format (likely XML).\n'
      printf 'First response bytes:\n'
      head -c 200 "$sessions_tmp" | cat
      printf '\nTip: verify server URL and authentication settings in .env.\n'
      rm -f "$sessions_tmp" "$err_tmp"
      return 1
    fi
    printf 'Players seen in current sessions:\n'
    jq -r '.MediaContainer.Metadata // [] | (if type == "array" then . else [.] end)[] | [.Player.title, .Player.device, .Player.state, .title, .grandparentTitle] | @tsv' "$sessions_tmp" \
      | awk -F '\t' '{printf("- player=%s | device=%s | state=%s | track=%s | artist=%s\n", $1, $2, $3, $4, $5)}' || true
  else
    printf 'Plex sessions response received (jq not installed, cannot parse player list).\n'
    printf 'Install jq or run: %s install\n' "$(basename "$0")"
  fi
  rm -f "$sessions_tmp" "$err_tmp"
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
  1. Open http://<PLEX_SERVER_IP>:32400/web in a browser.
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

  local python_exec
  python_exec="$(command -v "$PYTHON_BIN" || true)"
  if [[ -z "$python_exec" ]]; then
    log "Python executable '$PYTHON_BIN' not found"
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
ExecStart=$python_exec $APP_DIR/plexlcd.py
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
  install         Install dependencies
  configure       Create or update $ENV_FILE interactively
  test            Test Plex connectivity and list players from current sessions
  fb              Show framebuffer devices
  service         Install and start systemd service
  all             Run install, configure, test, fb, and service
EOFUSAGE
}

main() {
  local cmd="${1:-}"
  if [[ -z "$cmd" ]]; then
    usage
    printf '\nNext steps:\n'
    printf '  1) %s configure\n' "$(basename "$0")"
    printf '  2) %s test\n' "$(basename "$0")"
    printf '  3) %s service\n' "$(basename "$0")"
    return 0
  fi

  case "$cmd" in
    help) show_token_help ;;
    install) install_packages; prepare_local_files ;;
    configure) write_env ;;
    test) test_plex ;;
    fb) show_framebuffers ;;
    service) install_service ;;
    all)
      install_packages
      prepare_local_files
      write_env
      test_plex
      show_framebuffers
      install_service
      ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"