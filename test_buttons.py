#!/usr/bin/env python3
"""Standalone GPIO + Plex button test.

Run on the Pi:
    python3 test_buttons.py

Press each physical button. You should see "PIN XX PRESSED" immediately.
Then it will try to send a playPause command to Plex and show the response.
Press Ctrl+C to quit.
"""

import os
import sys
import time

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, ".env")
if os.path.isfile(env_path):
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    print(f"[env] Loaded {env_path}")
else:
    print(f"[env] WARNING: {env_path} not found")

PLEX_SERVER = os.environ.get("PLEX_SERVER", "http://plex.local:32400").rstrip("/")
PLEX_TOKEN  = os.environ.get("PLEX_TOKEN", "")
PINS = {
    int(os.environ.get("BUTTON_PLAY_PAUSE_PIN", "23")): "play_pause",
    int(os.environ.get("BUTTON_STOP_PIN",       "24")): "stop",
    int(os.environ.get("BUTTON_NEXT_PIN",        "25")): "next",
}
BOUNCE = float(os.environ.get("BUTTON_BOUNCE_TIME", "0.15"))

print(f"[env] PLEX_SERVER={PLEX_SERVER}")
print(f"[env] PLEX_TOKEN={'SET' if PLEX_TOKEN else 'MISSING'}")
print(f"[env] Pins: {PINS}  bounce={BOUNCE}s")

# ---------------------------------------------------------------------------
# Check gpiozero
# ---------------------------------------------------------------------------
try:
    from gpiozero import Button
    print("[gpio] gpiozero imported OK")
except ImportError as e:
    print(f"[gpio] FAILED to import gpiozero: {e}")
    print("       Install with:  sudo apt install python3-gpiozero")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Check pin factory (running without hardware will raise here)
# ---------------------------------------------------------------------------
try:
    from gpiozero.pins.rpigpio import RPiGPIOFactory
    from gpiozero import Device
    factory = RPiGPIOFactory()
    Device.pin_factory = factory
    print("[gpio] RPiGPIO pin factory set OK")
except Exception as e:
    print(f"[gpio] RPiGPIO factory not available ({e}); falling back to default factory")
    print("       If you are NOT on a Pi this will fail. If you ARE on a Pi, try:")
    print("       sudo apt install python3-rpi.gpio")

# ---------------------------------------------------------------------------
# Plex test helper
# ---------------------------------------------------------------------------
def test_plex_command(action: str, player_addr: str | None, player_port: int):
    """Fire a single Plex playback command directly to the player and print the full response."""
    endpoint_map = {"play_pause": "playPause", "stop": "stop", "next": "skipNext"}
    endpoint = endpoint_map.get(action, action)

    if not player_addr:
        print(f"[plex]  No player address known – skipping Plex call")
        return

    url = f"http://{player_addr}:{player_port}/player/playback/{endpoint}"
    params = {"type": "music", "commandID": int(time.time())}
    headers = {
        "Accept": "application/json",
        "X-Plex-Token": PLEX_TOKEN,
    }
    print(f"[plex]  GET {url}")
    print(f"[plex]  params={params}")
    try:
        import requests
        r = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"[plex]  HTTP {r.status_code}  body={r.text[:400]!r}")
    except Exception as exc:
        print(f"[plex]  ERROR: {exc}")


# ---------------------------------------------------------------------------
# Fetch current target client id from sessions
# ---------------------------------------------------------------------------
def get_target_client_id() -> tuple[str | None, str | None, int]:
    """Returns (machine_identifier, player_address, player_port)."""
    try:
        import requests
        player_name = os.environ.get("PLAYER_NAME", "")
        r = requests.get(
            f"{PLEX_SERVER}/status/sessions",
            headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN},
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("MediaContainer", {}).get("Metadata", [])
        if isinstance(items, dict):
            items = [items]
        for item in items:
            p = item.get("Player", {})
            title = (p.get("title") or p.get("name") or "").strip()
            mid = p.get("machineIdentifier", "")
            addr = p.get("address", "")
            port = int(p.get("port") or 32500)
            print(f"[plex]  Session player: title={title!r}  machineIdentifier={mid!r}  address={addr!r}  port={port}")
            if title == player_name:
                print(f"[plex]  Matched! direct URL will be http://{addr}:{port}")
                return mid, addr, port
        print(f"[plex]  No session matched PLAYER_NAME={player_name!r}")
    except Exception as exc:
        print(f"[plex]  Sessions fetch error: {exc}")
    return None, None, 32500


print()
print("[plex] Fetching current session to get target client id …")
target_id, player_addr, player_port = get_target_client_id()

# ---------------------------------------------------------------------------
# Set up buttons
# ---------------------------------------------------------------------------
buttons = []
print()
print("[gpio] Setting up buttons …")
for pin, action in PINS.items():
    try:
        btn = Button(pin, pull_up=True, bounce_time=BOUNCE)
        def _handler(a=action, p=pin):
            print(f"\n*** GPIO PIN {p} PRESSED  action={a} ***")
            test_plex_command(a, player_addr, player_port)
        btn.when_pressed = _handler
        buttons.append(btn)
        print(f"[gpio] Pin {pin} → {action}  OK")
    except Exception as e:
        print(f"[gpio] Pin {pin} FAILED: {e}")

print()
print("Ready. Press a physical button (Ctrl+C to quit) …")
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\n[test] Done.")
