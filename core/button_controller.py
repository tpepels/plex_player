"""Button command dispatch and GPIO wiring helpers."""

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from core.models import RuntimeState


@dataclass(frozen=True)
class ButtonControllerConfig:
    buttons_enabled: bool
    play_pause_pin: int
    stop_pin: int
    next_pin: int
    bounce_time: float
    plex_server: str
    plex_token: str
    controller_client_id: str
    http_timeout: int
    toast_duration_seconds: float
    no_track_grace_seconds: float
    command_confirm_seconds: float


def next_command_id(runtime_state: RuntimeState, command_counter_lock: threading.Lock) -> int:
    with command_counter_lock:
        runtime_state.command_counter += 1
        return runtime_state.command_counter


def dispatch_playback_command(
    action: str,
    *,
    config: ButtonControllerConfig,
    runtime_state: RuntimeState,
    refresh_event: threading.Event,
    command_counter_lock: threading.Lock,
    send_playback_request: Callable[..., bool],
    apply_button_rules: Callable[..., None],
    log_info: Callable[[str], None],
    log_warn: Callable[[str], None],
    log_debug: Callable[[str], None],
    log_error: Callable[[str], None],
    now_ts: float | None = None,
) -> None:
    cmd_id = next_command_id(runtime_state, command_counter_lock)
    sent_ok = send_playback_request(
        action=action,
        plex_server=config.plex_server,
        plex_token=config.plex_token,
        controller_client_id=config.controller_client_id,
        target_client_id=runtime_state.current_target_client_id,
        player_addr=runtime_state.current_player_address,
        player_port=runtime_state.current_player_port,
        command_id=cmd_id,
        timeout=config.http_timeout,
        log_info=log_info,
        log_warn=log_warn,
        log_debug=log_debug,
        log_error=log_error,
    )
    if not sent_ok:
        return

    action_ts = time.monotonic() if now_ts is None else now_ts
    apply_button_rules(
        runtime_state,
        action,
        command_id=cmd_id,
        now_ts=action_ts,
        toast_duration_seconds=config.toast_duration_seconds,
        stop_force_idle_seconds=max(2.0, config.no_track_grace_seconds),
        confirm_timeout_seconds=config.command_confirm_seconds,
    )
    refresh_event.set()


def setup_gpio_buttons(
    *,
    button_class: Any,
    button_devices: list[Any],
    runtime_state: RuntimeState,
    config: ButtonControllerConfig,
    dispatch_action: Callable[[str], None],
    log_message: Callable[[str], None],
) -> None:
    if not config.buttons_enabled or button_class is None:
        return

    for action, pin in (("play_pause", config.play_pause_pin), ("stop", config.stop_pin), ("next", config.next_pin)):
        button = button_class(pin, pull_up=True, bounce_time=config.bounce_time)

        def handler(action_name: str = action, pin_no: int = pin) -> None:
            log_message(f"GPIO pin {pin_no} pressed -> action={action_name} client_id={runtime_state.current_target_client_id!r}")
            dispatch_action(action_name)

        button.when_pressed = handler
        button_devices.append(button)

    log_message(
        f"Enabled GPIO buttons: play/pause={config.play_pause_pin}, stop={config.stop_pin}, next={config.next_pin}"
    )