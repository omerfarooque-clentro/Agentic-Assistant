from django.test import TestCase

from agent.cards.builders import (
    build_calendar_event,
    build_email_list,
    build_search_summary,
    build_sheet_update,
    build_sheet_view,
    build_slack_mentions,
    build_weather,
)


class BuildersUnitTests(TestCase):
    def test_build_weather_valid(self):
        payload = {
            "city": "Tokyo",
            "country": "Japan",
            "updated_at": "2026-09-19 12:00 UTC",
            "current": {
                "temp": 22.0,
                "unit": "°C",
                "condition": "clear",
                "feels_like": 21.5,
                "humidity": 55,
                "wind": "10 km/h",
                "rain_chance": 5,
            },
            "days": [
                {
                    "date": "2026-09-19",
                    "label": "Today",
                    "condition": "clear",
                    "low": 18.0,
                    "high": 25.0,
                    "rain_chance": 5,
                }
            ],
        }
        res = build_weather(payload)
        self.assertIsNotNone(res)
        self.assertEqual(res["city"], "Tokyo")

    def test_build_weather_invalid(self):
        self.assertIsNone(build_weather(None))
        self.assertIsNone(build_weather("error: failed"))
        self.assertIsNone(build_weather({"needs_clarification": True}))

    def test_build_email_list_json(self):
        payload = {
            "items": [
                {
                    "id": "18f12345",
                    "from": "Alice Smith <alice@example.com>",
                    "subject": "Q3 Planning",
                    "snippet": "Let's align on the goals for next quarter.",
                    "date": "2026-09-19",
                    "unread": True,
                }
            ]
        }
        res = build_email_list(payload)
        self.assertIsNotNone(res)
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["unread"], 1)
        self.assertEqual(res["items"][0]["sender_name"], "Alice Smith")
        self.assertEqual(res["items"][0]["sender_email"], "alice@example.com")

    def test_build_email_list_plain_text(self):
        plain_text = """Found 2 messages matching 'project':

📧 MESSAGES:
  1. Message ID: 18f999
     Subject: Status Update
     From: Bob Jones <bob@example.com>
     Date: Fri, 18 Sep 2026 14:00:00 -0400

  2. Message ID: 18f888
     Subject: Review PR
     From: Charlie <charlie@example.com>
     Date: Fri, 18 Sep 2026 15:00:00 -0400
"""
        res = build_email_list(plain_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["items"][0]["id"], "18f999")
        self.assertEqual(res["items"][0]["sender_name"], "Bob Jones")
        self.assertEqual(res["items"][1]["sender_name"], "Charlie")

    def test_build_email_list_empty(self):
        self.assertIsNone(build_email_list(""))
        self.assertIsNone(build_email_list("No messages found for query: 'xyz'"))

    def test_build_slack_mentions_json(self):
        payload = {
            "messages": [
                {
                    "channel": "dev-team",
                    "user": "Dave",
                    "text": "Hey @here please review the new card design",
                    "ts": "1726750000",
                }
            ]
        }
        res = build_slack_mentions(payload)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["channel"], "dev-team")

    def test_build_sheet_view_text(self):
        plain_text = """Successfully read 3 rows from range 'Sheet1!A1:C3' in spreadsheet 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms for user@example.com:
Row  1: ['Task', 'Assignee', 'Status']
Row  2: ['Design UI', 'Alice', 'Done']
Row  3: ['Implement API', 'Bob', 'In Progress']
"""
        res = build_sheet_view(plain_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["title"], "Sheet1!A1:C3")
        self.assertEqual(res["columns"], ["Task", "Assignee", "Status"])
        self.assertEqual(len(res["rows"]), 2)
        self.assertIn("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", res["url"])

    def test_build_sheet_update_text(self):
        plain_text = "Successfully updated range 'Sheet1!A4:C4' in spreadsheet 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms for user@example.com. Updated: 3 cells, 1 rows, 3 columns."
        res = build_sheet_update(plain_text)
        self.assertIsNotNone(res)
        self.assertEqual(res["row_number"], 4)
        self.assertIn("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms", res["url"])

    def test_build_calendar_event_text(self):
        plain_text = """Event Details:
- Title: Product Review
- Starts: 2026-09-20 14:00:00
- Ends: 2026-09-20 15:00:00
- Link: https://calendar.google.com/event?eid=123
"""
        res = build_calendar_event(plain_text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 1)
        self.assertEqual(res["events"][0]["title"], "Product Review")
        self.assertEqual(res["events"][0]["start"], "2026-09-20T14:00:00")
        self.assertIsNone(res["events"][0]["meet_url"])
        self.assertIsNone(res["events"][0]["status"])

    def test_calendar_read_basic(self):
        text = """- "Weekly Sync" (Starts: 2026-09-22 10:00:00, Ends: 2026-09-22 11:00:00)
  Meeting: https://meet.google.com/abc-defg-hij
  Link: https://calendar.google.com/calendar/event?eid=123"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 1)
        ev = res["events"][0]
        self.assertEqual(ev["title"], "Weekly Sync")
        self.assertEqual(ev["start"], "2026-09-22T10:00:00")
        self.assertEqual(ev["meet_url"], "https://meet.google.com/abc-defg-hij")
        self.assertEqual(ev["html_link"], "https://calendar.google.com/calendar/event?eid=123")
        self.assertIsNone(ev["status"])  # Read event does not have confirmed status

    def test_calendar_read_detailed(self):
        text = """Event: Team Retrospective
Start: 2026-09-23 15:00:00 [weekday: Wednesday; ISO weekday: 3]
End: 2026-09-23 16:00:00 [weekday: Wednesday; ISO weekday: 3]
Meeting: https://meet.google.com/xyz-uvwx-rst
Attendees: alice@example.com, bob@example.com
Link: https://calendar.google.com/calendar/event?eid=456"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 1)
        ev = res["events"][0]
        self.assertEqual(ev["title"], "Team Retrospective")
        self.assertEqual(ev["start"], "2026-09-23T15:00:00")
        self.assertEqual(ev["end"], "2026-09-23T16:00:00")
        self.assertEqual(ev["meet_url"], "https://meet.google.com/xyz-uvwx-rst")
        self.assertEqual(ev["attendees"], ["alice@example.com", "bob@example.com"])
        self.assertIsNone(ev["status"])

    def test_calendar_create_with_meet(self):
        text = """Event created successfully!
Summary: Sprint Planning
Starts: 2026-09-24 09:00:00
Ends: 2026-09-24 10:00:00
Google Meet: https://meet.google.com/jkl-mnop-qrs
Link: https://calendar.google.com/calendar/event?eid=789"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 1)
        ev = res["events"][0]
        self.assertEqual(ev["title"], "Sprint Planning")
        self.assertEqual(ev["meet_url"], "https://meet.google.com/jkl-mnop-qrs")
        self.assertEqual(ev["status"], "confirmed")

    def test_calendar_modify_with_conference(self):
        text = """Event updated successfully:
Title: Client Demo
Start: 2026-09-25 11:00:00
End: 2026-09-25 12:00:00
Conference: https://zoom.us/j/1234567890
Link: https://calendar.google.com/calendar/event?eid=999"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 1)
        ev = res["events"][0]
        self.assertEqual(ev["title"], "Client Demo")
        self.assertEqual(ev["meet_url"], "https://zoom.us/j/1234567890")
        self.assertEqual(ev["status"], "confirmed")

    def test_calendar_multi_event_scoped_links(self):
        text = """- "Standup" (Starts: 2026-09-22 09:00:00, Ends: 2026-09-22 09:15:00)
  Meeting: https://meet.google.com/aaa-bbbb-ccc
  Link: https://calendar.google.com/event1

- "Focus Time" (Starts: 2026-09-22 10:00:00, Ends: 2026-09-22 12:00:00)

- "Architecture Review" (Starts: 2026-09-22 14:00:00, Ends: 2026-09-22 15:00:00)
  Meeting: https://teams.microsoft.com/l/meetup-join/19%3ameeting
  Link: https://calendar.google.com/event3"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertEqual(len(res["events"]), 3)

        ev0 = res["events"][0]
        self.assertEqual(ev0["title"], "Standup")
        self.assertEqual(ev0["meet_url"], "https://meet.google.com/aaa-bbbb-ccc")
        self.assertEqual(ev0["html_link"], "https://calendar.google.com/event1")

        ev1 = res["events"][1]
        self.assertEqual(ev1["title"], "Focus Time")
        self.assertIsNone(ev1["meet_url"])
        self.assertIsNone(ev1["html_link"])

        ev2 = res["events"][2]
        self.assertEqual(ev2["title"], "Architecture Review")
        self.assertEqual(ev2["meet_url"], "https://teams.microsoft.com/l/meetup-join/19%3ameeting")
        self.assertEqual(ev2["html_link"], "https://calendar.google.com/event3")

    def test_calendar_invalid_meet_hosts_dropped(self):
        text = """- "Suspicious Event" (Starts: 2026-09-22 10:00:00, Ends: 2026-09-22 11:00:00)
  Meeting: https://phishing.com/meet/123"""
        res = build_calendar_event(text)
        self.assertIsNotNone(res)
        self.assertIsNone(res["events"][0]["meet_url"])

    def test_build_search_summary_json(self):
        payload = {
            "query": "LangGraph features",
            "results": [
                {
                    "title": "LangGraph Docs",
                    "url": "https://langchain-ai.github.io/langgraph/",
                    "content": "LangGraph is a library for building stateful, multi-actor applications with LLMs.",
                }
            ],
        }
        res = build_search_summary(payload)
        self.assertIsNotNone(res)
        self.assertEqual(res["source_count"], 1)
        self.assertEqual(res["sources"][0]["domain"], "langchain-ai.github.io")
        self.assertEqual(len(res["points"]), 1)
