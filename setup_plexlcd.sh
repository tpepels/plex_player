#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
ENV_FILE="$SCRIPT_DIR/.env"
SERVICE_FILE="/etc/systemd/system/plexlcd.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

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

have_apt_pkg() {
  local pkg="$1"
  apt-cache show "$pkg" >/dev/null 2>&1
}

ensure_venv() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Creating virtual environment at $VENV_DIR (with system site packages)"
    "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
  fi

  log "Installing/updating Python dependencies in .venv"
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  if [[ -f "$APP_DIR/requirements.txt" ]]; then
    "$VENV_PYTHON" -m pip install -r "$APP_DIR/requirements.txt"
  else
    "$VENV_PYTHON" -m pip install pillow gpiozero
  fi

  # Optional GPIO compatibility helpers. Do not fail the install if one path is unavailable.
  if ! "$VENV_PYTHON" -c 'import lgpio' >/dev/null 2>&1; then
    if have_apt_pkg python3-lgpio; then
      log "python3-lgpio is available via apt; recreating venv with system packages is enough"
    elif "$VENV_PYTHON" -m pip show lgpio >/dev/null 2>&1; then
      :
    else
      log "lgpio Python module not present; continuing without forcing a source build"
    fi
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

  local packages=(
    python3 python3-pip python3-venv python3-dev
    python3-pil python3-gpiozero python3-rpi.gpio python3-pigpio
    python3-libgpiod gpiod curl jq fonts-dejavu-core fonts-noto-core fbset fbi
  )

  if have_apt_pkg python3-lgpio; then
    packages+=(python3-lgpio)
  fi
  if have_apt_pkg swig; then
    packages+=(swig)
  fi

  $SUDO apt-get install -y "${packages[@]}"

  # rpi-lgpio docs warn not to keep python3-rpi.gpio and rpi-lgpio in the same environment.
  # Only install rpi-lgpio if explicitly requested and python3-lgpio is present.
  if [[ "${INSTALL_RPI_LGPIO:-0}" == "1" ]]; then
    log "INSTALL_RPI_LGPIO=1 detected; installing rpi-lgpio into the venv"
    if have_apt_pkg python3-rpi.gpio; then
      $SUDO apt-get remove -y python3-rpi.gpio || true
    fi
    ensure_venv
    "$VENV_PYTHON" -m pip install rpi-lgpio
  else
    ensure_venv
  fi
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

quote_env_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
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

upsert_env_value() {
  local key="$1"
  local value="$2"
  local tmp_file line replaced=0
  local quoted_value
  quoted_value="$(quote_env_value "$value")"
  tmp_file="$(mktemp "${ENV_FILE}.XXXXXX")"

  if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" == "$key="* ]]; then
        printf '%s=%s\n' "$key" "$quoted_value" >>"$tmp_file"
        replaced=1
      else
        printf '%s\n' "$line" >>"$tmp_file"
      fi
    done < "$ENV_FILE"
  fi

  if [[ "$replaced" -eq 0 ]]; then
    printf '%s=%s\n' "$key" "$quoted_value" >>"$tmp_file"
  fi

  mv "$tmp_file" "$ENV_FILE"
  chmod 600 "$ENV_FILE" || true
}

write_env() {
  mkdir -p "$APP_DIR"

  if [[ -f "$ENV_FILE" ]]; then
    load_env_file "$ENV_FILE" || true
  fi

  local plex_host player_name plex_token latitude longitude timezone location_name fb_device width height poll_seconds weather_refresh display_x_shift buttons_enabled button_play_pause_pin button_stop_pin button_next_pin button_label_play_y_percent button_label_stop_y_percent button_label_next_y_percent progress_update_seconds no_track_grace_seconds startup_trace startup_log gpiozero_pin_factory

  plex_host="${PLEX_SERVER:-http://plex.local:32400}"
  player_name="${PLAYER_NAME:-}"
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
  gpiozero_pin_factory="${GPIOZERO_PIN_FACTORY:-lgpio}"

  log "Basic setup (only the essentials)"
  plex_host=$(prompt_default "Plex server URL" "$plex_host")
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
    gpiozero_pin_factory=$(prompt_default "GPIOZERO_PIN_FACTORY" "$gpiozero_pin_factory")
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

    local -a players
    mapfile -t players < <(
      jq -r '.MediaContainer.Metadata // [] | (if type == "array" then . else [.] end)[] | (.Player.title // .Player.name // empty)' "$sessions_tmp" \
        | awk 'NF && !seen[$0]++'
    )

    if [[ "${#players[@]}" -eq 0 ]]; then
      log "No active players found. Start playback on the Pi and rerun $(basename "$0") configure to save PLAYER_NAME."
    elif [[ "${#players[@]}" -eq 1 ]]; then
      if [[ "${PLAYER_NAME:-}" == "${players[0]}" ]]; then
        log "PLAYER_NAME already matches the active player: ${players[0]}"
      elif prompt_yes_no "Use '${players[0]}' as PLAYER_NAME in $ENV_FILE" 0; then
        upsert_env_value "PLAYER_NAME" "${players[0]}"
        export PLAYER_NAME="${players[0]}"
        log "Saved PLAYER_NAME='${players[0]}' to $ENV_FILE"
      fi
    else
      local i choice selected_player
      printf '\nSelect the player this screen should follow:\n'
      for i in "${!players[@]}"; do
        printf '  %d) %s' "$((i + 1))" "${players[$i]}"
        if [[ "${PLAYER_NAME:-}" == "${players[$i]}" ]]; then
          printf ' (current)'
        fi
        printf '\n'
      done
      read -r -p "Choose player number to save to .env [Enter to keep current]: " choice || true
      if [[ -n "${choice:-}" ]]; then
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#players[@]} )); then
          selected_player="${players[$((choice - 1))]}"
          upsert_env_value "PLAYER_NAME" "$selected_player"
          export PLAYER_NAME="$selected_player"
          log "Saved PLAYER_NAME='$selected_player' to $ENV_FILE"
        else
          log "Ignoring invalid selection; keeping current PLAYER_NAME"
        fi
      fi
    fi
  else
    printf 'Plex sessions response received (jq not installed, cannot parse player list).\n'
    printf 'Install jq or run: %s install\n' "$(basename "$0")"
  fi
  rm -f "$sessions_tmp" "$err_tmp"
}

configure_app() {
  local offer_service_install="${1:-1}"

  log "Configure writes .env and detects the active Plex player."
  prepare_local_files
  if [[ ! -x "$VENV_PYTHON" ]]; then
    log "Virtual environment not found. Run $(basename "$0") install first."
    return 1
  fi
  write_env
  printf '\nBefore the next step, start playback on the Pi if you want automatic player detection.\n\n'
  test_plex

  load_env_file "$ENV_FILE" || true
  if [[ -z "${PLAYER_NAME:-}" ]]; then
    log "PLAYER_NAME is still empty. Start playback on the Pi, then rerun: $(basename "$0") configure"
    return 0
  fi

  if [[ "$offer_service_install" != "1" ]]; then
    log "Configuration complete."
    return 0
  fi

  if prompt_yes_no "Install and start the systemd service now" 0; then
    install_service
  else
    log "Configuration complete. Rerun $(basename "$0") install or $(basename "$0") service when you want to install the service."
  fi
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

  if [[ ! -x "$VENV_PYTHON" ]]; then
    ensure_venv
  fi
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
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
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

install_app() {
  install_packages
  prepare_local_files

  if [[ ! -f "$ENV_FILE" ]]; then
    log "No $ENV_FILE found. Running configure so install can also create the service."
    configure_app 0 || return 1
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    log "Skipping service installation because $ENV_FILE was not created."
    return 1
  fi

  install_service
}

usage() {
  cat <<EOFUSAGE
Usage: $(basename "$0") [command]

Commands:
  install         Install packages, create/update .venv, and install/restart the service
  configure       Write .env and detect the player
  service         Reinstall/restart only the systemd service from the current tree
  framebuffers    Show detected framebuffer devices
  token-help      Print Plex token instructions
EOFUSAGE
}

main() {
  local cmd="${1:-}"
  if [[ -z "$cmd" ]]; then
    usage
    printf '\nRecommended setup:\n'
    printf '  %s install\n' "$(basename "$0")"
    printf '\nOptional later updates:\n'
    printf '  %s configure\n' "$(basename "$0")"
    printf '  %s service\n' "$(basename "$0")"
    return 0
  fi

  case "$cmd" in
    install) install_app ;;
    configure) configure_app ;;
    service) install_service ;;
    framebuffers) show_framebuffers ;;
    token-help) show_token_help ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
