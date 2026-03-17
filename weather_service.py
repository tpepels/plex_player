"""Weather API service functions and weather label/symbol lookups.

Design assumptions:
- Open-Meteo is queried as a best-effort data source for idle screen enhancement.
- Weather failures should degrade gracefully (no hard app failure).
- Lookup maps are UI-facing defaults and can be replaced without changing fetch logic.
"""

import time
from typing import Callable, Optional

import requests

from models import WeatherInfo

WEATHER_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Violent showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "T-storm + hail",
    99: "Heavy hail",
}

WEATHER_SYMBOLS = {
    0: ("☀", "☾"),
    1: ("🌤", "☾"),
    2: ("⛅", "☁"),
    3: ("☁", "☁"),
    45: ("🌫", "🌫"),
    48: ("🌫", "🌫"),
    51: ("🌦", "🌧"),
    53: ("🌦", "🌧"),
    55: ("🌧", "🌧"),
    61: ("🌦", "🌧"),
    63: ("🌧", "🌧"),
    65: ("🌧", "🌧"),
    71: ("🌨", "🌨"),
    73: ("🌨", "🌨"),
    75: ("❄", "❄"),
    77: ("❄", "❄"),
    80: ("🌦", "🌧"),
    81: ("🌧", "🌧"),
    82: ("⛈", "⛈"),
    85: ("🌨", "🌨"),
    86: ("❄", "❄"),
    95: ("⛈", "⛈"),
    96: ("⛈", "⛈"),
    99: ("⛈", "⛈"),
}


def get_weather_symbol(weather_code: int, is_day: int) -> str:
    """Return day/night symbol for weather code, with safe fallback."""

    day_symbol, night_symbol = WEATHER_SYMBOLS.get(weather_code, ("?", "?"))
    return day_symbol if is_day else night_symbol


def fetch_weather(
    latitude: float,
    longitude: float,
    timezone: str,
    timeout: int,
    log_warn: Callable[[str], None],
    log_error: Callable[[str, Exception], None],
) -> Optional[WeatherInfo]:
    """Fetch weather with retry logic. Returns None on failure.

    Assumptions:
    - Only a compact subset of weather data is required for display.
    - Hourly index `[1]` is interpreted as "next hour" based on forecast window.
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,weather_code,is_day,relative_humidity_2m"
        "&hourly=temperature_2m,weather_code"
        "&forecast_hours=2"
        "&daily=temperature_2m_min,temperature_2m_max"
        "&forecast_days=1"
        f"&timezone={timezone}"
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            payload = r.json()
            cur = payload.get("current", {})
            daily = payload.get("daily", {})
            hourly = payload.get("hourly", {})

            humidity = cur.get("relative_humidity_2m")
            min_vals = daily.get("temperature_2m_min") or []
            max_vals = daily.get("temperature_2m_max") or []
            next_temps = hourly.get("temperature_2m") or []
            next_codes = hourly.get("weather_code") or []

            return WeatherInfo(
                temp_c=float(cur["temperature_2m"]),
                weather_code=int(cur["weather_code"]),
                is_day=int(cur["is_day"]),
                humidity_pct=int(humidity) if humidity is not None else None,
                temp_min_c=float(min_vals[0]) if min_vals else None,
                temp_max_c=float(max_vals[0]) if max_vals else None,
                next_hour_temp_c=float(next_temps[1]) if len(next_temps) > 1 else None,
                next_hour_weather_code=int(next_codes[1]) if len(next_codes) > 1 else None,
            )
        except Exception as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                log_warn(f"Attempt {attempt + 1}/{max_retries} failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                log_error(f"Failed after {max_retries} attempts", exc)
    return None
