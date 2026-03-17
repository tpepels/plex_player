"""Plex API service functions.

Design assumptions:
- The app polls `status/sessions` and treats Plex as source-of-truth for playback state.
- Network failures are expected and handled with short retry/backoff windows.
- Functions in this module stay UI-agnostic and return normalized model objects for renderer code.
"""

import io
import time
from typing import Callable, Optional

import requests
from PIL import Image, ImageOps

from models import PlexTrack


def normalize_playback_state(item: dict) -> str:
    """Return raw playback state reported by Plex player/session."""
    player = item.get("Player", {})
    session = item.get("Session", {})

    player_state = str(player.get("state") or "").strip().lower()
    session_state = str(session.get("state") or "").strip().lower()

    # Some clients keep Player.state as "playing" while Session.state moves to
    # paused/stopped. Preserve those non-playing transitions so UI can leave the
    # now-playing screen immediately.
    if player_state == "playing" and session_state in {"paused", "stopped"}:
        return session_state

    if player_state and player_state != "none":
        return player_state
    if session_state and session_state != "none":
        return session_state
    return "unknown"


def playback_status_text(state: str) -> str:
    """Map Plex raw state to concise UI label text."""

    mapping = {
        "playing": "Playing",
        "paused": "Paused",
        "stopped": "Stopped",
        "buffering": "Buffering",
    }
    return mapping.get(state, state.capitalize() if state else "Stopped")


def fetch_sessions_json(
    plex_server: str,
    plex_token: str,
    timeout: int,
    log_warn: Callable[[str], None],
    log_error: Callable[[str], None],
) -> Optional[dict]:
    """Fetch active Plex sessions as JSON.

    Assumptions:
    - A valid token is required.
    - Occasional transient failures are normal and should not crash the main loop.
    """

    if not plex_token:
        log_error("Missing PLEX_TOKEN")
        return None

    max_retries = 2
    for attempt in range(max_retries):
        try:
            r = requests.get(
                f"{plex_server}/status/sessions",
                params={"X-Plex-Token": plex_token},
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_warn(f"Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                log_error(f"Sessions failed after {max_retries} attempts: {exc}")
    return None


def find_player_track(data: dict, player_name: str) -> Optional[PlexTrack]:
    """Locate the session entry for the configured player name.

    Assumptions:
    - `player_name` must exactly match Plex session player title/name.
    - Some metadata fields may be absent; caller must handle partial track payloads.
    """

    container = data.get("MediaContainer", {})
    metadata = container.get("Metadata", [])
    if isinstance(metadata, dict):
        metadata = [metadata]

    for item in metadata:
        player = item.get("Player", {})
        title = (player.get("title") or player.get("name") or "").strip()
        if title == player_name:
            media = item.get("Media") or []
            media0 = media[0] if isinstance(media, list) and media else {}
            duration = item.get("duration") or media0.get("duration")
            view_offset = item.get("viewOffset")
            thumb = (
                item.get("thumb")
                or item.get("grandparentThumb")
                or item.get("parentThumb")
                or item.get("art")
            )
            return PlexTrack(
                title=item.get("title", "Unknown Track"),
                artist=item.get("grandparentTitle", "Unknown Artist"),
                album=item.get("parentTitle", "Unknown Album"),
                thumb_path=thumb,
                state=normalize_playback_state(item),
                target_client_identifier=(
                    player.get("machineIdentifier")
                    or player.get("clientIdentifier")
                    or item.get("machineIdentifier")
                ),
                player_address=player.get("address"),
                player_port=int(player.get("port") or 32500),
                elapsed_ms=int(view_offset) if view_offset is not None else None,
                duration_ms=int(duration) if duration is not None else None,
            )
    return None


def fetch_cover(
    thumb_path: str,
    plex_server: str,
    plex_token: str,
    width: int,
    height: int,
    timeout: int,
    log_warn: Callable[[str], None],
    log_error: Callable[[str], None],
) -> Optional[Image.Image]:
    """Fetch and resize cover art to framebuffer dimensions.

    Assumptions:
    - Plex path thumbs may require token query parameter when using local relative paths.
    - Returning `None` is acceptable and should trigger placeholder rendering.
    """

    if not thumb_path:
        return None

    max_retries = 2
    for attempt in range(max_retries):
        try:
            direct_url = thumb_path if thumb_path.startswith(("http://", "https://")) else f"{plex_server}{thumb_path}"
            params = None
            if not thumb_path.startswith(("http://", "https://")):
                params = {"X-Plex-Token": plex_token}

            r = requests.get(
                direct_url,
                params=params,
                headers={"Accept": "image/*", "X-Plex-Token": plex_token},
                timeout=timeout,
            )
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.size != (width, height):
                img = ImageOps.fit(img.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
            return img
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_warn(f"Cover attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                log_error(f"Cover failed after {max_retries} attempts: {exc}")
    return None


def send_playback_command(
    action: str,
    plex_server: str,
    plex_token: str,
    controller_client_id: str,
    target_client_id: Optional[str],
    player_addr: Optional[str],
    player_port: int,
    command_id: int,
    timeout: int,
    log_info: Callable[[str], None],
    log_warn: Callable[[str], None],
    log_debug: Callable[[str], None],
    log_error: Callable[[str], None],
) -> bool:
    """Send a playback command to the active player endpoint.

    Assumptions:
    - Player direct endpoint (`http://<player_addr>:<port>`) is preferred when available.
    - Boolean success/failure is enough for caller; detailed diagnostics are emitted via logger callbacks.
    """

    endpoint_map = {
        "play_pause": "playPause",
        "stop": "stop",
        "next": "skipNext",
    }
    endpoint = endpoint_map.get(action)
    if not endpoint:
        log_warn(f"Ignoring unknown action: {action}")
        return False

    if not target_client_id:
        log_warn(f"Ignoring {action}: no active Plex target client")
        return False

    if player_addr:
        base_url = f"http://{player_addr}:{player_port}"
    else:
        base_url = plex_server
        log_warn("No player address known, falling back to server URL")

    url = f"{base_url}/player/playback/{endpoint}"
    log_info(f"{action} -> GET {url} commandID={command_id}")
    try:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "X-Plex-Token": plex_token,
                "X-Plex-Client-Identifier": controller_client_id,
            },
            params={
                "type": "music",
                "commandID": command_id,
            },
            timeout=timeout,
        )
        log_debug(f"Response {resp.status_code}: {resp.text[:200]!r}")
        resp.raise_for_status()
        return True
    except Exception as exc:
        log_error(f"Failed to send {action}: {exc}")
        return False
