"""Plex API service functions.

Design assumptions:
- The app polls `status/sessions` and treats Plex as source-of-truth for playback state.
- Network failures are expected and handled with short retry/backoff windows.
- Functions in this module stay UI-agnostic and return normalized model objects for renderer code.
"""

import io
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional

from PIL import Image, ImageOps

from core.models import PlexTrack
from .http_client import get as http_get


def normalize_playback_state(item: dict) -> str:
    """Return raw playback state reported by Plex player/session."""
    player = item.get("Player", {})
    session = item.get("Session", {})

    player_state = str(player.get("state") or "").strip().lower()
    session_state = str(session.get("state") or "").strip().lower()

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


def _get_with_retry(
    url: str,
    *,
    params: Optional[dict],
    headers: dict,
    timeout: int,
    label: str,
    log_warn: Callable[[str], None],
    log_error: Callable[[str], None],
):
    for attempt in range(2):
        try:
            r = http_get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as exc:
            if attempt == 0:
                log_warn(f"{label} failed: {exc}. Retrying...")
                time.sleep(1)
            else:
                log_error(f"{label} failed after 2 attempts: {exc}")
    return None


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

    r = _get_with_retry(
        f"{plex_server}/status/sessions",
        params={"X-Plex-Token": plex_token},
        headers={"Accept": "application/json"},
        timeout=timeout,
        label="Sessions",
        log_warn=log_warn,
        log_error=log_error,
    )
    return r.json() if r else None


def _to_int(v: object) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
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
            state = normalize_playback_state(item)
            duration_i = _to_int(duration)
            view_offset_i = _to_int(view_offset)

            player_state_raw = str(player.get("state") or "").strip().lower() or None
            session_state_raw = str(item.get("Session", {}).get("state") or "").strip().lower() or None

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
                state=state,
                target_client_identifier=(
                    player.get("machineIdentifier")
                    or player.get("clientIdentifier")
                    or item.get("machineIdentifier")
                ),
                player_address=player.get("address"),
                player_port=int(player.get("port") or 32500),
                player_state_raw=player_state_raw,
                session_state_raw=session_state_raw,
                elapsed_ms=view_offset_i,
                duration_ms=duration_i,
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
    is_absolute = thumb_path.startswith(("http://", "https://"))
    r = _get_with_retry(
        thumb_path if is_absolute else f"{plex_server}{thumb_path}",
        params=None if is_absolute else {"X-Plex-Token": plex_token},
        headers={"Accept": "image/*", "X-Plex-Token": plex_token},
        timeout=timeout,
        label="Cover",
        log_warn=log_warn,
        log_error=log_error,
    )
    if not r:
        return None
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    if img.size != (width, height):
        img = ImageOps.fit(img.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    return img


def fetch_player_timeline_state(
    player_addr: Optional[str],
    player_port: int,
    plex_token: str,
    timeout: int,
    log_warn: Callable[[str], None],
) -> Optional[dict]:
    """Poll Plexamp player's timeline endpoint for current playback state.

    Returns a dict with optional keys: `state`, `time_ms`, `duration_ms`.
    """

    if not player_addr or not plex_token:
        return None

    try:
        url = f"http://{player_addr}:{player_port}/player/timeline/poll"
        resp = http_get(
            url,
            headers={
                "Accept": "application/xml, text/xml, */*",
                "X-Plex-Token": plex_token,
            },
            params={
                "wait": 0,
                "commandID": 1,
                "type": "music",
            },
            timeout=timeout,
        )

        root = ET.fromstring(resp.content)
        timeline = root.find("Timeline")
        if timeline is None:
            return None

        state = str(timeline.attrib.get("state") or "").strip().lower() or None
        time_raw = timeline.attrib.get("time")
        duration_raw = timeline.attrib.get("duration")

        time_ms = _to_int(time_raw)
        duration_ms = _to_int(duration_raw)

        return {
            "state": state,
            "time_ms": time_ms,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        log_warn(f"Timeline poll failed: {exc}")
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
        resp = http_get(
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
        return True
    except Exception as exc:
        log_error(f"Failed to send {action}: {exc}")
        return False
