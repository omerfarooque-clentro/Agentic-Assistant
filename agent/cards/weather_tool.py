import logging
from datetime import datetime, timezone
import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

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
    """Get real-time weather and 7-day forecast for a given city and optional country using Open-Meteo.
    Always use this tool instead of web search for weather queries.
    """
    if not city:
        return {"error": "City name is required"}

    try:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        geo_resp = requests.get(geo_url, params={"name": city.strip(), "count": 5, "format": "json"}, timeout=10)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        results = geo_data.get("results") or []

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

        forecast_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
        }
        fc_resp = requests.get(forecast_url, params=params, timeout=10)
        fc_resp.raise_for_status()
        fc_data = fc_resp.json()

        current_raw = fc_data.get("current", {})
        daily_raw = fc_data.get("daily", {})

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
        }
    except Exception as e:
        logger.exception("Error in get_weather for %s: %s", city, e)
        return {"error": f"Failed to retrieve weather data: {str(e)}"}
