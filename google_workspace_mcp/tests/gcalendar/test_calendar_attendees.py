import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import _normalize_attendees


class CalendarAttendeeNormalizationTests(unittest.TestCase):
    def test_normalize_attendees_none_or_empty(self):
        self.assertIsNone(_normalize_attendees(None))
        self.assertIsNone(_normalize_attendees(""))
        self.assertIsNone(_normalize_attendees("   "))
        self.assertIsNone(_normalize_attendees([]))

    def test_normalize_attendees_single_email_string(self):
        # Passing a raw email string should NOT be split into characters
        result = _normalize_attendees("omer.farooque@yahoo.com")
        self.assertEqual(result, [{"email": "omer.farooque@yahoo.com"}])

    def test_normalize_attendees_comma_separated_string(self):
        result = _normalize_attendees("alice@example.com, bob@example.com, charlie@example.com")
        self.assertEqual(result, [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
            {"email": "charlie@example.com"},
        ])

    def test_normalize_attendees_json_array_string(self):
        json_str = '["alice@example.com", "bob@example.com"]'
        result = _normalize_attendees(json_str)
        self.assertEqual(result, [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ])

    def test_normalize_attendees_list_of_strings(self):
        result = _normalize_attendees(["alice@example.com", "bob@example.com", "  "])
        self.assertEqual(result, [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ])

    def test_normalize_attendees_list_of_dicts(self):
        attendees = [
            {"email": "alice@example.com", "responseStatus": "accepted"},
            {"email": "bob@example.com", "optional": True},
        ]
        result = _normalize_attendees(attendees)
        self.assertEqual(result, attendees)

    def test_normalize_attendees_dict_without_email_or_empty(self):
        attendees = [
            {"displayName": "Alice"},  # missing email
            {"email": ""},              # empty email
            {"email": None},            # None email
            {"email": "valid@example.com"},
        ]
        result = _normalize_attendees(attendees)
        self.assertEqual(result, [{"email": "valid@example.com"}])

    def test_normalize_attendees_mixed_list(self):
        attendees = [
            "alice@example.com",
            {"email": "bob@example.com", "responseStatus": "tentative"},
            12345,  # invalid format
        ]
        result = _normalize_attendees(attendees)
        self.assertEqual(result, [
            {"email": "alice@example.com"},
            {"email": "bob@example.com", "responseStatus": "tentative"},
        ])


if __name__ == "__main__":
    unittest.main()
