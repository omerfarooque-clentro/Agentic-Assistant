from unittest.mock import MagicMock, patch
from django.test import TestCase

from agent.tools.weather_tool import get_weather, map_wmo_code


class WeatherToolTests(TestCase):
    def test_wmo_code_mapping(self):
        self.assertEqual(map_wmo_code(0), "clear")
        self.assertEqual(map_wmo_code(1), "partly_cloudy")
        self.assertEqual(map_wmo_code(3), "cloudy")
        self.assertEqual(map_wmo_code(61), "rain")
        self.assertEqual(map_wmo_code(71), "snow")
        self.assertEqual(map_wmo_code(95), "thunderstorm")

    @patch("agent.cards.weather_tool.requests.get")
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

    @patch("agent.cards.weather_tool.requests.get")
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

    @patch("agent.cards.weather_tool.requests.get")
    def test_weather_location_not_found(self, mock_get):
        geo_mock = MagicMock()
        geo_mock.json.return_value = {"results": []}
        geo_mock.raise_for_status = MagicMock()
        mock_get.return_value = geo_mock

        result = get_weather.invoke({"city": "NonExistentCity12345"})
        self.assertIn("error", result)
