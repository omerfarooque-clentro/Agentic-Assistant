import json
from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.cards.result_cards import build_result_card


class ResultCardsOrchestratorTests(TestCase):
    def setUp(self):
        self.sample_weather_data = {
            "city": "Berlin",
            "country": "Germany",
            "updated_at": "2026-09-19 15:00 UTC",
            "current": {
                "temp": 19.2,
                "unit": "°C",
                "condition": "clear",
                "feels_like": 18.9,
                "humidity": 60,
                "wind": "15 km/h",
                "rain_chance": 0,
            },
            "days": [
                {
                    "date": "2026-09-19",
                    "label": "Today",
                    "condition": "clear",
                    "low": 12.0,
                    "high": 21.0,
                    "rain_chance": 0,
                }
            ],
        }

    def test_build_result_card_question_presentation(self):
        messages = [
            HumanMessage(content="What is the weather in Berlin?"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_weather", "args": {"city": "Berlin"}, "id": "call_1"}],
            ),
            ToolMessage(
                tool_call_id="call_1",
                name="get_weather",
                content=json.dumps(self.sample_weather_data),
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        self.assertEqual(card["kind"], "result")
        self.assertEqual(card["type"], "weather")
        self.assertEqual(card["presentation"]["order"], "text_first")
        self.assertEqual(card["data"]["city"], "Berlin")

    def test_build_result_card_command_presentation(self):
        messages = [
            HumanMessage(content="Check weather in Berlin"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_weather", "args": {"city": "Berlin"}, "id": "call_1"}],
            ),
            ToolMessage(
                tool_call_id="call_1",
                name="get_weather",
                content=json.dumps(self.sample_weather_data),
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        self.assertEqual(card["presentation"]["order"], "card_first")

    def test_build_result_card_isolates_current_turn(self):
        # Old turn had weather, new turn had search error
        messages = [
            HumanMessage(content="Check weather in Berlin"),
            ToolMessage(
                tool_call_id="call_0",
                name="get_weather",
                content=json.dumps(self.sample_weather_data),
            ),
            HumanMessage(content="Tell me a joke"),
            AIMessage(content="Why did the chicken cross the road?"),
        ]
        card = build_result_card(messages)
        self.assertIsNone(card)

    def test_build_result_card_ignores_error_tool_message(self):
        messages = [
            HumanMessage(content="Show recent emails"),
            ToolMessage(
                tool_call_id="call_err",
                name="search_gmail_messages",
                status="error",
                content="Error: Auth expired",
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNone(card)

    def test_build_result_card_never_raises_on_corrupt_data(self):
        messages = [
            HumanMessage(content="Hello"),
            ToolMessage(
                tool_call_id="call_weird",
                name="get_weather",
                content=object(),  # non-serializable object
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNone(card)

    def test_build_email_list_unwraps_mcp_text_blocks(self):
        mcp_content = [
            {
                "type": "text",
                "text": "Found 1 message:\n  1. Message ID: 19572b8c\n     Subject: Quarterly Review\n     From: Sarah Connor <sarah@skynet.com>\n     Date: Mon, 15 Mar 2026 10:00:00 GMT\n     Web Link: https://mail.google.com/mail/u/0/#inbox/19572b8c\n",
            }
        ]
        messages = [
            HumanMessage(content="search my emails"),
            ToolMessage(
                tool_call_id="call_mcp_email",
                name="search_gmail_messages",
                content=mcp_content,
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        self.assertEqual(card["type"], "email_list")
        self.assertEqual(len(card["data"]["items"]), 1)
        item = card["data"]["items"][0]
        self.assertEqual(item["subject"], "Quarterly Review")
        self.assertEqual(item["sender_name"], "Sarah Connor")
        self.assertEqual(item["sender_email"], "sarah@skynet.com")

    def test_build_slack_mentions_parses_search_results(self):
        slack_output = {
            "results": "# Search Results for: project update\n\n### Result 1 of 1\nChannel: general\nFrom: alice\nPermalink: https://myteam.slack.com/archives/C123/p456\nText:\nHere is the latest project update for everyone."
        }
        messages = [
            HumanMessage(content="search slack"),
            ToolMessage(
                tool_call_id="call_slack_search",
                name="slack_search_public_and_private",
                content=slack_output,
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        self.assertEqual(card["type"], "slack_mentions")
        self.assertEqual(len(card["data"]["items"]), 1)
        item = card["data"]["items"][0]
        self.assertEqual(item["channel"], "general")
        self.assertEqual(item["sender"], "alice")
        self.assertIn("latest project update", item["text"])
        self.assertEqual(item["permalink"], "https://myteam.slack.com/archives/C123/p456")

    def test_build_slack_mentions_cleans_metadata_and_context(self):
        slack_output = {
            "results": (
                "### Result 1 of 1\n"
                "Channel: DM (ID: D0BDRSYBQP8)\n"
                "From: Arsalan <arsalankamran@example.com> (ID: EXAMPLE_ID)\n"
                "Permalink: https://myteam.slack.com/example/example/example/example\n"
                "Text:\n"
                "Hey omer how are you? Context after: - From: Omer Farooque <omerfarooque@example.com> (ID: EXAMPLE_ID) Message_ts: 1787425690.117519 i'm good, thanks for asking. ---"
            )
        }
        messages = [
            HumanMessage(content="check slack"),
            ToolMessage(
                tool_call_id="call_slack_clean",
                name="slack_search_public_and_private",
                content=slack_output,
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        item = card["data"]["items"][0]
        self.assertEqual(item["channel"], "DM")
        self.assertEqual(item["sender"], "Arsalan")
        self.assertEqual(item["text"], "Hey omer how are you?")

    def test_build_calendar_event_creation_confirmation(self):
        cal_output = "Successfully created event 'Strategic Partnership Kickoff' for 2026-03-23T15:00:00 to 2026-03-23T16:00:00 (UTC). Attendees: alice@example.com, bob@example.com. Link: https://calendar.google.com/event?eid=xyz"
        messages = [
            HumanMessage(content="Schedule meeting"),
            ToolMessage(
                tool_call_id="call_cal_create",
                name="manage_event",
                content=cal_output,
            ),
        ]
        card = build_result_card(messages)
        self.assertIsNotNone(card)
        self.assertEqual(card["type"], "calendar_event")
        self.assertEqual(len(card["data"]["events"]), 1)
        event = card["data"]["events"][0]
        self.assertEqual(event["title"], "Strategic Partnership Kickoff")
        self.assertEqual(event["start"], "2026-03-23T15:00:00")
        self.assertEqual(event["end"], "2026-03-23T16:00:00")
        self.assertEqual(event["attendees"], ["alice@example.com", "bob@example.com"])
        self.assertEqual(event["html_link"], "https://calendar.google.com/event?eid=xyz")
