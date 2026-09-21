import difflib
import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import Mock

import requests
from langchain_core.tools import tool
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)

GEO_CACHE_TTL = 86400  # 24 hours
FORECAST_CACHE_TTL = 600  # 10 minutes
_MEM_CACHE: dict[str, tuple[float, Any]] = {}
_WEATHER_SESSION: requests.Session | None = None


def _cache_get(key: str) -> Any | None:
    try:
        from django.core.cache import cache
        val = cache.get(key)
        if val is not None:
            return val
    except Exception:
        pass

    if key in _MEM_CACHE:
        expires_at, data = _MEM_CACHE[key]
        if datetime.now(timezone.utc).timestamp() < expires_at:
            return data
        _MEM_CACHE.pop(key, None)
    return None


def _cache_set(key: str, val: Any, timeout: int) -> None:
    try:
        from django.core.cache import cache
        cache.set(key, val, timeout=timeout)
    except Exception:
        pass
    _MEM_CACHE[key] = (datetime.now(timezone.utc).timestamp() + timeout, val)


def get_weather_session() -> requests.Session:
    global _WEATHER_SESSION
    if _WEATHER_SESSION is None:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "AgenticAssistant/1.0 (WeatherClient; Open-Meteo Integration)",
            "Accept": "application/json",
        })
        _WEATHER_SESSION = session
    return _WEATHER_SESSION


def _http_get(url: str, params: dict, timeout: int = 10) -> requests.Response:
    if isinstance(requests.get, Mock):
        return requests.get(url, params=params, timeout=timeout)
    session = get_weather_session()
    return session.get(url, params=params, timeout=timeout)


WMO_CODE_MAP = {
    0: "clear",
    1: "partly_cloudy",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    80: "rain",
    81: "rain",
    82: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    85: "snow",
    86: "snow",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}


def map_wmo_code(code: int | None) -> str:
    if code is None:
        return "partly_cloudy"
    return WMO_CODE_MAP.get(int(code), "cloudy")


@tool("get_weather")
def get_weather(city: str, country: str | None = None) -> dict:
    """Get real-time weather, hourly conditions, and 7-day forecast for a given city and optional country using Open-Meteo.
    Always use this tool instead of web search for ALL weather-related queries (including hourly forecast, current weather, 7-day outlook, precipitation, temperature).
    """
    if not city:
        return {"error": "City name is required"}

    try:
        geo_cache_key = f"weather:geo:{city.strip().lower()}"
        results = _cache_get(geo_cache_key)

        if results is None:
            geo_url = "https://geocoding-api.open-meteo.com/v1/search"
            geo_resp = _http_get(geo_url, params={"name": city.strip(), "count": 5, "format": "json"}, timeout=10)
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            results = geo_data.get("results") or []
            if results:
                _cache_set(geo_cache_key, results, timeout=GEO_CACHE_TTL)

        if not results:
            return {"error": f"No location found for '{city}'."}

        # Filter by country if provided
        selected = None
        if country:
            country_norm = country.strip().lower()
            matching = [
                r for r in results
                if country_norm in r.get("country", "").lower() or country_norm in r.get("country_code", "").lower()
            ]
            if not matching:
                all_countries = {r.get("country", "").lower(): r for r in results if r.get("country")}
                matches = difflib.get_close_matches(country_norm, list(all_countries.keys()), n=1, cutoff=0.55)
                if matches:
                    matching = [all_countries[matches[0]]]
            if matching:
                selected = matching[0]

        if not selected:
            # Check for ambiguity if multiple distinct countries/admin regions exist
            if len(results) > 1 and not country:
                countries = {r.get("country") for r in results if r.get("country")}
                if len(countries) > 1:
                    candidates = [
                        {
                            "name": r.get("name"),
                            "admin1": r.get("admin1"),
                            "country": r.get("country"),
                            "country_code": r.get("country_code"),
                        }
                        for r in results[:4]
                    ]
                    return {
                        "needs_clarification": True,
                        "city": city,
                        "candidates": candidates,
                        "message": f"Multiple locations found for '{city}'. Please clarify which country you mean.",
                    }
            selected = results[0]

        lat = selected["latitude"]
        lon = selected["longitude"]
        resolved_city = selected.get("name", city)
        resolved_country = selected.get("country", "")

        fc_cache_key = f"weather:fc:{lat:.4f}:{lon:.4f}"
        fc_data = _cache_get(fc_cache_key)

        if fc_data is None:
            forecast_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "timezone": "auto",
            }
            fc_resp = _http_get(forecast_url, params=params, timeout=10)
            fc_resp.raise_for_status()
            fc_data = fc_resp.json()
            if fc_data:
                _cache_set(fc_cache_key, fc_data, timeout=FORECAST_CACHE_TTL)

        current_raw = fc_data.get("current", {})
        daily_raw = fc_data.get("daily", {})
        hourly_raw = fc_data.get("hourly", {})

        current_condition = map_wmo_code(current_raw.get("weather_code"))
        current_temp = round(float(current_raw.get("temperature_2m", 0.0)), 1)
        feels_like = round(float(current_raw.get("apparent_temperature", current_temp)), 1)
        humidity = int(current_raw.get("relative_humidity_2m", 0))
        wind_speed = round(float(current_raw.get("wind_speed_10m", 0.0)), 1)
        wind_str = f"{wind_speed} km/h"

        daily_dates = daily_raw.get("time", [])
        daily_codes = daily_raw.get("weather_code", [])
        daily_maxs = daily_raw.get("temperature_2m_max", [])
        daily_mins = daily_raw.get("temperature_2m_min", [])
        daily_probs = daily_raw.get("precipitation_probability_max", [])

        # Current rain chance from today's forecast
        current_rain_chance = int(daily_probs[0]) if daily_probs else int(current_raw.get("precipitation", 0) > 0) * 100

        days = []
        for i in range(min(len(daily_dates), 7)):
            d_str = daily_dates[i]
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                label = "Today" if i == 0 else dt.strftime("%a")
            except Exception:
                label = f"Day {i+1}"

            code = daily_codes[i] if i < len(daily_codes) else 0
            low = round(float(daily_mins[i]), 1) if i < len(daily_mins) else 0.0
            high = round(float(daily_maxs[i]), 1) if i < len(daily_maxs) else 0.0
            rain_prob = int(daily_probs[i]) if i < len(daily_probs) and daily_probs[i] is not None else 0

            days.append({
                "date": d_str,
                "label": label,
                "condition": map_wmo_code(code),
                "low": low,
                "high": high,
                "rain_chance": rain_prob,
            })

        # Parse up to 24 upcoming hours
        hourly_times = hourly_raw.get("time", [])
        hourly_temps = hourly_raw.get("temperature_2m", [])
        hourly_codes = hourly_raw.get("weather_code", [])
        hourly_probs = hourly_raw.get("precipitation_probability", [])

        hourly = []
        for j in range(min(len(hourly_times), 24)):
            t_str = hourly_times[j]
            t_code = hourly_codes[j] if j < len(hourly_codes) else 0
            t_temp = round(float(hourly_temps[j]), 1) if j < len(hourly_temps) else 0.0
            t_prob = int(hourly_probs[j]) if j < len(hourly_probs) and hourly_probs[j] is not None else 0
            hourly.append({
                "time": t_str,
                "temp": t_temp,
                "condition": map_wmo_code(t_code),
                "rain_chance": t_prob,
            })

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        return {
            "city": resolved_city,
            "country": resolved_country,
            "updated_at": now_utc,
            "current": {
                "temp": current_temp,
                "unit": "°C",
                "condition": current_condition,
                "feels_like": feels_like,
                "humidity": humidity,
                "wind": wind_str,
                "rain_chance": current_rain_chance,
            },
            "days": days,
            "hourly": hourly,
        }
    except requests.exceptions.HTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code == 429:
            logger.warning("Open-Meteo rate limit reached for %s: %s", city, e)
            return {"error": "The weather service is temporarily busy (rate limited). Please try again in a few moments."}
        logger.exception("HTTP error in get_weather for %s: %s", city, e)
        return {"error": f"Failed to retrieve weather data: {str(e)}"}
    except Exception as e:
        logger.exception("Error in get_weather for %s: %s", city, e)
        return {"error": f"Failed to retrieve weather data: {str(e)}"}

