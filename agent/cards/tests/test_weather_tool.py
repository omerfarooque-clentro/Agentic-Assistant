from unittest.mock import MagicMock, patch
import requests
from django.core.cache import cache
from django.test import TestCase

from agent.tools.weather_tool import _MEM_CACHE, get_weather, map_wmo_code


class WeatherToolTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        _MEM_CACHE.clear()

    def tearDown(self):
        cache.clear()
        _MEM_CACHE.clear()
        super().tearDown()

    def test_wmo_code_mapping(self):
        self.assertEqual(map_wmo_code(0), "clear")
        self.assertEqual(map_wmo_code(1), "partly_cloudy")
        self.assertEqual(map_wmo_code(3), "cloudy")
        self.assertEqual(map_wmo_code(61), "rain")
        self.assertEqual(map_wmo_code(71), "snow")
        self.assertEqual(map_wmo_code(95), "thunderstorm")

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_success(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {
            "results": [
                {
                    "name": "London",
                    "country": "United Kingdom",
                    "latitude": 51.5,
                    "longitude": -0.12,
                }
            ]
        }
        geo_mock.raise_for_status = MagicMock()

        fc_mock = MagicMock()
        fc_mock.json.return_value = {
            "current": {
                "temperature_2m": 15.4,
                "apparent_temperature": 14.1,
                "relative_humidity_2m": 72,
                "weather_code": 1,
                "wind_speed_10m": 12.5,
            },
            "daily": {
                "time": ["2026-09-19", "2026-09-20"],
                "weather_code": [1, 61],
                "temperature_2m_max": [17.0, 14.0],
                "temperature_2m_min": [10.0, 9.0],
                "precipitation_probability_max": [10, 80],
            },
        }
        fc_mock.raise_for_status = MagicMock()

        mock_get.side_effect = [geo_mock, fc_mock]

        result = get_weather.invoke({"city": "London"})
        self.assertEqual(result["city"], "London")
        self.assertEqual(result["country"], "United Kingdom")
        self.assertEqual(result["current"]["temp"], 15.4)
        self.assertEqual(result["current"]["condition"], "partly_cloudy")
        self.assertEqual(len(result["days"]), 2)
        self.assertEqual(result["days"][0]["label"], "Today")

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_caching(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {
            "results": [
                {
                    "name": "Paris",
                    "country": "France",
                    "latitude": 48.85,
                    "longitude": 2.35,
                }
            ]
        }
        geo_mock.raise_for_status = MagicMock()

        fc_mock = MagicMock()
        fc_mock.json.return_value = {
            "current": {"temperature_2m": 20.0, "weather_code": 0},
            "daily": {},
            "hourly": {},
        }
        fc_mock.raise_for_status = MagicMock()

        mock_get.side_effect = [geo_mock, fc_mock]

        res1 = get_weather.invoke({"city": "Paris", "country": "France"})
        self.assertEqual(res1["city"], "Paris")
        self.assertEqual(mock_get.call_count, 2)

        # Second call should be served from cache without extra HTTP calls
        res2 = get_weather.invoke({"city": "Paris", "country": "France"})
        self.assertEqual(res2["city"], "Paris")
        self.assertEqual(mock_get.call_count, 2)

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_country_fuzzy_match(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {
            "results": [
                {"name": "Hyderabad", "country": "India", "latitude": 17.3, "longitude": 78.4},
                {"name": "Hyderabad", "country": "Pakistan", "latitude": 25.3, "longitude": 68.3},
            ]
        }
        geo_mock.raise_for_status = MagicMock()

        fc_mock = MagicMock()
        fc_mock.json.return_value = {
            "current": {"temperature_2m": 31.0, "weather_code": 0},
            "daily": {},
            "hourly": {},
        }
        fc_mock.raise_for_status = MagicMock()
        mock_get.side_effect = [geo_mock, fc_mock]

        # Typo: "pakisatn" should fuzzy match "Pakistan"
        result = get_weather.invoke({"city": "Hyderabad", "country": "pakisatn"})
        self.assertEqual(result["country"], "Pakistan")

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_disambiguation(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {
            "results": [
                {"name": "Hyderabad", "country": "India", "admin1": "Telangana", "latitude": 17.3, "longitude": 78.4},
                {"name": "Hyderabad", "country": "Pakistan", "admin1": "Sindh", "latitude": 25.3, "longitude": 68.3},
            ]
        }
        geo_mock.raise_for_status = MagicMock()
        mock_get.return_value = geo_mock

        result = get_weather.invoke({"city": "Hyderabad"})
        self.assertTrue(result.get("needs_clarification"))
        self.assertIn("candidates", result)
        self.assertEqual(len(result["candidates"]), 2)

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_location_not_found(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {"results": []}
        geo_mock.raise_for_status = MagicMock()
        mock_get.return_value = geo_mock

        result = get_weather.invoke({"city": "NonExistentCity12345"})
        self.assertIn("error", result)

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_rate_limit_handled(self, mock_get):
        geo_mock = MagicMock()
        response_429 = requests.Response()
        response_429.status_code = 429
        error_429 = requests.exceptions.HTTPError(response=response_429)
        geo_mock.raise_for_status.side_effect = error_429
        mock_get.return_value = geo_mock

        result = get_weather.invoke({"city": "Tokyo"})
        self.assertIn("error", result)
        self.assertIn("temporarily busy", result["error"])

    @patch("agent.tools.weather_tool.requests.get")
    def test_weather_day_details_and_hourly(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {
            "results": [
                {
                    "name": "Karachi",
                    "country": "Pakistan",
                    "latitude": 24.86,
                    "longitude": 67.01,
                }
            ]
        }
        geo_mock.raise_for_status = MagicMock()

        fc_mock = MagicMock()
        fc_mock.json.return_value = {
            "current": {
                "temperature_2m": 27.0,
                "apparent_temperature": 32.0,
                "relative_humidity_2m": 80,
                "weather_code": 0,
                "wind_speed_10m": 6.8,
                "surface_pressure": 1008.0,
                "is_day": 0,
            },
            "daily": {
                "time": ["2026-09-22"],
                "weather_code": [0],
                "temperature_2m_max": [34.0],
                "temperature_2m_min": [27.0],
                "precipitation_probability_max": [2],
                "sunrise": ["2026-09-22T06:12"],
                "sunset": ["2026-09-22T18:34"],
                "uv_index_max": [6.0],
            },
            "hourly": {
                "time": ["2026-09-22T00:00", "2026-09-22T01:00"],
                "temperature_2m": [27.0, 26.5],
                "weather_code": [0, 1],
                "precipitation_probability": [0, 5],
                "apparent_temperature": [32.0, 31.0],
                "uv_index": [0.0, 0.0],
                "is_day": [0, 0],
            },
        }
        fc_mock.raise_for_status = MagicMock()
        mock_get.side_effect = [geo_mock, fc_mock]

        result = get_weather.invoke({"city": "Karachi", "country": "Pakistan"})
        self.assertEqual(result["current"]["pressure"], 1008.0)
        self.assertEqual(result["current"]["uv_index"], 6.0)
        self.assertEqual(result["current"]["is_day"], 0)
        self.assertEqual(len(result["days"]), 1)
        day0 = result["days"][0]
        self.assertEqual(day0["sunrise"], "2026-09-22T06:12")
        self.assertEqual(day0["sunset"], "2026-09-22T18:34")
        self.assertEqual(day0["uv_index_max"], 6.0)
        self.assertEqual(len(day0["hourly"]), 2)
        self.assertEqual(day0["hourly"][0]["rain_chance"], 0)
        self.assertEqual(day0["hourly"][0]["is_day"], 0)
        self.assertEqual(day0["hourly"][1]["rain_chance"], 5)
        self.assertEqual(day0["hourly"][1]["is_day"], 0)


