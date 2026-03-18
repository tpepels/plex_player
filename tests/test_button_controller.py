import threading
import unittest

from core import ButtonControllerConfig, RuntimeState, dispatch_playback_command, next_command_id, setup_gpio_buttons


class FakeButton:
    def __init__(self, pin, pull_up=True, bounce_time=0.0):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None


class ButtonControllerTests(unittest.TestCase):
    def _config(self, **kwargs) -> ButtonControllerConfig:
        data = {
            "buttons_enabled": True,
            "play_pause_pin": 23,
            "stop_pin": 24,
            "next_pin": 25,
            "bounce_time": 0.15,
            "plex_server": "http://plex.local:32400",
            "plex_token": "token",
            "controller_client_id": "controller-1",
            "http_timeout": 10,
            "toast_duration_seconds": 0.7,
            "no_track_grace_seconds": 4.0,
            "command_confirm_seconds": 5.0,
        }
        data.update(kwargs)
        return ButtonControllerConfig(**data)

    def test_next_command_id_increments_runtime_counter(self):
        runtime = RuntimeState(command_counter=10)
        self.assertEqual(next_command_id(runtime, threading.Lock()), 11)
        self.assertEqual(runtime.command_counter, 11)

    def test_dispatch_updates_runtime_and_sets_refresh(self):
        runtime = RuntimeState(current_target_client_id="client-1", current_player_address="127.0.0.1")
        refresh = threading.Event()
        recorded = {}

        def send_playback_request(**kwargs):
            recorded.update(kwargs)
            return True

        def apply_rules(runtime_state, action, **kwargs):
            runtime_state.toast_text = action
            runtime_state.toast_until_ts = kwargs["now_ts"] + kwargs["toast_duration_seconds"]

        dispatch_playback_command(
            "stop",
            config=self._config(),
            runtime_state=runtime,
            refresh_event=refresh,
            command_counter_lock=threading.Lock(),
            send_playback_request=send_playback_request,
            apply_button_rules=apply_rules,
            log_info=lambda msg: None,
            log_warn=lambda msg: None,
            log_debug=lambda msg: None,
            log_error=lambda msg: None,
            now_ts=100.0,
        )

        self.assertEqual(recorded["action"], "stop")
        self.assertEqual(recorded["target_client_id"], "client-1")
        self.assertTrue(refresh.is_set())
        self.assertEqual(runtime.toast_text, "stop")

    def test_setup_gpio_buttons_registers_handlers(self):
        runtime = RuntimeState(current_target_client_id="client-9")
        devices = []
        dispatched = []
        logs = []

        setup_gpio_buttons(
            button_class=FakeButton,
            button_devices=devices,
            runtime_state=runtime,
            config=self._config(),
            dispatch_action=dispatched.append,
            log_message=logs.append,
        )

        self.assertEqual([d.pin for d in devices], [23, 24, 25])
        devices[1].when_pressed()
        self.assertEqual(dispatched, ["stop"])
        self.assertTrue(any("Enabled GPIO buttons" in msg for msg in logs))
        self.assertTrue(any("action=stop" in msg for msg in logs))


if __name__ == "__main__":
    unittest.main()