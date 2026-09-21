import json
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.cards.result_cards import build_result_card
from conversations.models import Message, Thread


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

    @patch("agent.runner.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.runner.get_user_tools", new_callable=AsyncMock)
    @patch("agent.runner.create_graph")
    async def test_runner_emits_tool_start_and_tool_end(self, mock_create_graph, mock_get_tools, mock_ensure_cp):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = await User.objects.acreate_user(username="runneruser", email="runner@example.com")
        thread = await Thread.objects.acreate(name="Test Thread", user=user)
        mock_get_tools.return_value = {"research": []}

        weather_json = json.dumps(self.sample_weather_data)

        async def fake_astream_events(*args, **kwargs):
            # 1. Tool starts
            yield {
                "event": "on_tool_start",
                "name": "get_weather",
                "metadata": {"langgraph_node": "tools"},
            }
            # 2. Tool ends with output
            yield {
                "event": "on_tool_end",
                "name": "get_weather",
                "metadata": {"langgraph_node": "tools"},
                "data": {"output": ToolMessage(content=weather_json, name="get_weather", tool_call_id="call_w")},
            }
            # 3. Agent streams tokens
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "general_agent"},
                "data": {"chunk": AIMessage(content="Here is the weather.")},
            }

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [
                HumanMessage(content="weather in Berlin"),
                ToolMessage(content=weather_json, name="get_weather", tool_call_id="call_w"),
                AIMessage(content="Here is the weather."),
            ],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        from agent.runner import run_agent
        events = []
        async for item in run_agent(message="weather in Berlin", thread_id=thread.id, user=user):
            events.append(item)

        types = [e["type"] for e in events]
        self.assertIn("tool_start", types)
        self.assertIn("tool_end", types)
        self.assertIn("token", types)
        self.assertIn("completed", types)

        start_event = next(e for e in events if e["type"] == "tool_start")
        self.assertEqual(start_event["tool"], "get_weather")

        end_event = next(e for e in events if e["type"] == "tool_end")
        self.assertEqual(end_event["tool"], "get_weather")

    @patch("agent.streaming.run_agent")
    async def test_event_stream_translates_tool_events_to_cards(self, mock_run_agent):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = await User.objects.acreate_user(username="streamuser", email="stream@example.com")
        thread = await Thread.objects.acreate(name="Test Thread", user=user)
        weather_json = json.dumps(self.sample_weather_data)

        async def fake_run_agent(*args, **kwargs):
            yield {"type": "tool_start", "tool": "get_weather"}
            yield {"type": "tool_end", "tool": "get_weather", "output": ToolMessage(content=weather_json, name="get_weather", tool_call_id="call_w")}
            yield {"type": "token", "token": "Weather is sunny."}
            yield {
                "type": "completed",
                "result": {"messages": [AIMessage(content="Weather is sunny.")]},
                "metrics": {},
            }

        mock_run_agent.side_effect = fake_run_agent

        from agent.streaming import event_stream
        sse_events = []
        async for chunk in event_stream(formatted_message="weather in Berlin", thread=thread, user=user):
            if chunk.startswith("data: "):
                sse_events.append(json.loads(chunk[6:].strip()))

        sse_types = [e["type"] for e in sse_events]
        self.assertIn("card_loading", sse_types)
        self.assertIn("result_card", sse_types)
        self.assertIn("token", sse_types)
        self.assertIn("completed", sse_types)

        # Ensure result_card is NOT duplicated
        self.assertEqual(sse_types.count("result_card"), 1)

        card_event = next(e for e in sse_events if e["type"] == "result_card")
        self.assertEqual(card_event["card"]["type"], "weather")
        self.assertEqual(card_event["card"]["data"]["city"], "Berlin")

        # Ensure message is saved to DB with the card attached
        saved_msg = await Message.objects.filter(thread=thread, role="agent").alast()
        self.assertIsNotNone(saved_msg)
        self.assertIsNotNone(saved_msg.cards)
        self.assertEqual(saved_msg.cards[0]["type"], "weather")

