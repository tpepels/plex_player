#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
ENV_FILE="$SCRIPT_DIR/.env"
SERVICE_FILE="/etc/systemd/system/plexlcd.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

ensure_venv() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Creating virtual environment at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  log "Installing/updating Python dependencies in .venv"
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  if [[ -f "$APP_DIR/requirements.txt" ]]; then
    "$VENV_PYTHON" -m pip install -r "$APP_DIR/requirements.txt"
  else
    "$VENV_PYTHON" -m pip install requests pillow numpy
  fi
}

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
    curl jq fonts-dejavu-core fonts-noto-core fbset fbi

  ensure_venv
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

prompt_yes_no() {
  local prompt="$1"
  local default_no="${2:-1}"
  local answer
  if [[ "$default_no" -eq 1 ]]; then
    read -r -p "$prompt [y/N]: " answer || true
  else
    read -r -p "$prompt [Y/n]: " answer || true
  fi
  answer="${answer,,}"
  if [[ -z "$answer" ]]; then
    if [[ "$default_no" -eq 1 ]]; then
      return 1
    fi
    return 0
  fi
  [[ "$answer" == "y" || "$answer" == "yes" ]]
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

  if [[ -f "$ENV_FILE" ]]; then
    load_env_file "$ENV_FILE" || true
  fi

  local plex_host player_name plex_token latitude longitude timezone location_name fb_device width height poll_seconds weather_refresh display_x_shift buttons_enabled button_play_pause_pin button_stop_pin button_next_pin button_label_play_y_percent button_label_stop_y_percent button_label_next_y_percent progress_update_seconds no_track_grace_seconds startup_trace startup_log gpiozero_pin_factory

  # Baseline defaults (or existing values when re-running configure)
  plex_host="${PLEX_SERVER:-http://plex.local:32400}"
  player_name="${PLAYER_NAME:-Plexamp Pi Zero}"
  plex_token="${PLEX_TOKEN:-}"
  latitude="${LATITUDE:-0.0000}"
  longitude="${LONGITUDE:-0.0000}"
  timezone="${TIMEZONE:-UTC}"
  location_name="${LOCATION_NAME:-}"
  fb_device="${FB_DEVICE:-/dev/fb1}"
  width="${WIDTH:-320}"
  height="${HEIGHT:-240}"
  display_x_shift="${DISPLAY_X_SHIFT:-0}"
  buttons_enabled="${BUTTONS_ENABLED:-1}"
  button_play_pause_pin="${BUTTON_PLAY_PAUSE_PIN:-23}"
  button_stop_pin="${BUTTON_STOP_PIN:-24}"
  button_next_pin="${BUTTON_NEXT_PIN:-25}"
  button_label_play_y_percent="${BUTTON_LABEL_PLAY_Y_PERCENT:-20}"
  button_label_stop_y_percent="${BUTTON_LABEL_STOP_Y_PERCENT:-40}"
  button_label_next_y_percent="${BUTTON_LABEL_NEXT_Y_PERCENT:-60}"
  poll_seconds="${POLL_SECONDS:-3}"
  weather_refresh="${WEATHER_REFRESH_SECONDS:-900}"
  progress_update_seconds="${PROGRESS_UPDATE_SECONDS:-5}"
  no_track_grace_seconds="${NO_TRACK_GRACE_SECONDS:-4.0}"
  startup_trace="${PLEXLCD_STARTUP_TRACE:-0}"
  startup_log="${PLEXLCD_STARTUP_LOG:-/tmp/plexlcd-startup.log}"
  gpiozero_pin_factory="${GPIOZERO_PIN_FACTORY:-auto}"

  log "Minimal setup (required fields only)"
  plex_host=$(prompt_default "Plex server URL" "$plex_host")
  player_name=$(prompt_default "Exact Plexamp player name" "$player_name")
  if [[ -n "$plex_token" ]] && prompt_yes_no "Keep existing Plex token" 1; then
    :
  else
    plex_token=$(prompt_secret "Plex token")
  fi

  if [[ -z "$plex_token" ]]; then
    log "PLEX_TOKEN cannot be empty"
    return 1
  fi

  if prompt_yes_no "Configure advanced settings (weather/display/buttons)?" 1; then
    latitude=$(prompt_default "Latitude" "$latitude")
    longitude=$(prompt_default "Longitude" "$longitude")
    timezone=$(prompt_default "Timezone" "$timezone")
    location_name=$(prompt_default "Location name shown on display" "$location_name")
    fb_device=$(prompt_default "Framebuffer device" "$fb_device")
    width=$(prompt_default "Display width" "$width")
    height=$(prompt_default "Display height" "$height")
    display_x_shift=$(prompt_default "Display X shift (pixels, negative/positive)" "$display_x_shift")
    buttons_enabled=$(prompt_default "Enable GPIO buttons (0/1)" "$buttons_enabled")
    button_play_pause_pin=$(prompt_default "Play/Pause button GPIO pin" "$button_play_pause_pin")
    button_stop_pin=$(prompt_default "Stop button GPIO pin" "$button_stop_pin")
    button_next_pin=$(prompt_default "Next button GPIO pin" "$button_next_pin")
    button_label_play_y_percent=$(prompt_default "Play/Pause label Y percent" "$button_label_play_y_percent")
    button_label_stop_y_percent=$(prompt_default "Stop label Y percent" "$button_label_stop_y_percent")
    button_label_next_y_percent=$(prompt_default "Next label Y percent" "$button_label_next_y_percent")
    poll_seconds=$(prompt_default "Plex poll interval seconds" "$poll_seconds")
    weather_refresh=$(prompt_default "Weather refresh seconds" "$weather_refresh")
    progress_update_seconds=$(prompt_default "Progress update seconds" "$progress_update_seconds")
    no_track_grace_seconds=$(prompt_default "No-track grace seconds" "$no_track_grace_seconds")
  else
    log "Using defaults for weather/display/buttons. You can re-run configure anytime."
  fi

  cat > "$ENV_FILE" <<EOFENV
PLEX_SERVER="$plex_host"
PLEX_TOKEN="$plex_token"
PLAYER_NAME="$player_name"
LATITUDE="$latitude"
LONGITUDE="$longitude"
TIMEZONE="$timezone"
LOCATION_NAME="$location_name"
FB_DEVICE="$fb_device"
WIDTH="$width"
HEIGHT="$height"
DISPLAY_X_SHIFT="$display_x_shift"
BUTTONS_ENABLED="$buttons_enabled"
BUTTON_PLAY_PAUSE_PIN="$button_play_pause_pin"
BUTTON_STOP_PIN="$button_stop_pin"
BUTTON_NEXT_PIN="$button_next_pin"
BUTTON_LABEL_PLAY_Y_PERCENT="$button_label_play_y_percent"
BUTTON_LABEL_STOP_Y_PERCENT="$button_label_stop_y_percent"
BUTTON_LABEL_NEXT_Y_PERCENT="$button_label_next_y_percent"
POLL_SECONDS="$poll_seconds"
WEATHER_REFRESH_SECONDS="$weather_refresh"
PROGRESS_UPDATE_SECONDS="$progress_update_seconds"
NO_TRACK_GRACE_SECONDS="$no_track_grace_seconds"
PLEXLCD_STARTUP_TRACE="$startup_trace"
PLEXLCD_STARTUP_LOG="$startup_log"
GPIOZERO_PIN_FACTORY="$gpiozero_pin_factory"
EOFENV

  chmod 600 "$ENV_FILE"
  log "Wrote $ENV_FILE"
  log "Next: $(basename "$0") test"
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

  ensure_venv
  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Virtual environment Python not found at $VENV_PYTHON"
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
ExecStart=$VENV_PYTHON $APP_DIR/plexlcd.py
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
  venv            Create/update .venv and install Python dependencies
  configure       Guided setup (minimal prompts + optional advanced)
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
    venv) ensure_venv ;;
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