from unittest.mock import patch

from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.llm.messages import MAX_TOOL_MESSAGE_CHARS, messages_for_llm
from agent.routing.query_generator import has_conversational_reference
from agent.routing.intent_router import route_intent
from agent.llm.prompts import get_system_prompt, BASE_SYSTEM_PROMPT


class MessagesForLlmTests(TestCase):
    def test_omits_tool_output_from_completed_turns(self):
        state = {
            "messages": [
                HumanMessage(content="Find the latest news"),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "old"}]),
                ToolMessage(content="old " * 10000, tool_call_id="old"),
                AIMessage(content="Here are the results."),
                HumanMessage(content="okay"),
            ]
        }

        messages = messages_for_llm(state)

        self.assertEqual(messages[-1].content, "okay")
        self.assertNotIn("old old old", "\n".join(str(message.content) for message in messages))

    def test_keeps_active_tool_result_but_bounds_its_size(self):
        state = {
            "messages": [
                HumanMessage(content="Search for updates"),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "current"}]),
                ToolMessage(content="x" * (MAX_TOOL_MESSAGE_CHARS * 2), tool_call_id="current"),
            ]
        }

        messages = messages_for_llm(state)

        tool_message = messages[-1]
        self.assertLessEqual(len(tool_message.content), MAX_TOOL_MESSAGE_CHARS + 100)
        self.assertIn("[Tool output truncated for context]", tool_message.content)

    def test_domain_scoped_prompts(self):
        state = {"messages": [HumanMessage(content="Send an email to John")]}
        email_messages = messages_for_llm(state, domain="email")
        self.assertIn("EMAIL GUIDELINES", email_messages[0].content)
        self.assertNotIn("SPREADSHEET GUIDELINES", email_messages[0].content)

        general_messages = messages_for_llm(state, domain="general")
        self.assertEqual(general_messages[0].content, BASE_SYSTEM_PROMPT)

    def test_general_prompt_with_available_domains(self):
        state = {
            "messages": [HumanMessage(content="What tools are available?")],
            "available_domains": {"email", "calendar", "slack"},
        }
        general_messages = messages_for_llm(state, domain="general")
        prompt_text = str(general_messages[0].content)

        self.assertIn("AVAILABLE TOOLS:", prompt_text)
        self.assertIn("Gmail", prompt_text)
        self.assertIn("Google Calendar", prompt_text)
        self.assertIn("Slack", prompt_text)
        self.assertNotIn("Web Search", prompt_text)

    def test_cross_domain_context_preservation(self):
        # Sequence: Turn 1 queries Sheets -> Turn 2 asks to send that to Slack
        state = {
            "messages": [
                HumanMessage(content="Read Q3 revenue from spreadsheet"),
                AIMessage(content="Q3 Revenue is $1.2M according to the spreadsheet."),
                HumanMessage(content="Send that to Sarah on Slack"),
            ]
        }
        slack_messages = messages_for_llm(state, domain="slack")
        # Ensure previous turn's assistant answer is preserved in prompt context for Slack agent
        history_text = "\n".join(str(m.content) for m in slack_messages)
        self.assertIn("Q3 Revenue is $1.2M", history_text)
        self.assertIn("Send that to Sarah on Slack", history_text)


class ReferenceDetectorTests(TestCase):
    def test_detects_standalone_queries_without_references(self):
        self.assertFalse(has_conversational_reference("search my emails for travel tickets"))
        self.assertFalse(has_conversational_reference("what meetings do I have tomorrow?"))
        self.assertFalse(has_conversational_reference("create a new document called Roadmap"))
        self.assertFalse(has_conversational_reference("summarize the report"))

    def test_detects_conversational_references_and_pronouns(self):
        self.assertTrue(has_conversational_reference("send it to Ahmed on Slack"))
        self.assertTrue(has_conversational_reference("email that to Sarah"))
        self.assertTrue(has_conversational_reference("schedule the same meeting again"))
        self.assertTrue(has_conversational_reference("can you forward those to my manager?"))
        self.assertTrue(has_conversational_reference("send it"))
        self.assertTrue(has_conversational_reference("yes do it"))


class AdaptiveIntentRouterTests(TestCase):
    def test_standalone_query_bypasses_call1(self):
        # Standalone 1-turn query should skip Call #1 rewrite (call_metrics is None)
        result = route_intent(
            [HumanMessage(content="search my unread emails")],
            available_domains={"email", "calendar", "docs", "sheets", "slack", "research"},
        )
        self.assertEqual(result["domain"], "email")
        self.assertIsNone(result["call_metrics"])

    def test_general_conversation_classification(self):
        test_queries = [
            "hello, how are you?",
            "how are you doing today?",
            "What operations tasks can you assist me with?",
            "what tools are available",
            "what tools do you have access to?",
            "who are you and what do you do?",
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        for q in test_queries:
            result = route_intent(
                [HumanMessage(content=q)],
                available_domains=available_domains,
            )
            self.assertEqual(result["intent"], "general", f"Failed on query: {q}")
            self.assertEqual(result["domain"], "general", f"Failed on query: {q}")
            self.assertEqual(result["status"], "confident", f"Failed on query: {q}")
            self.assertIsNone(result["call_metrics"], f"Should not trigger Call #1 rewrite for standalone {q}")


class StreamTitleFilterTests(TestCase):
    def test_filter_removes_tag_and_extracts_title(self):
        from agent.llm.titles import StreamTitleFilter, extract_title_from_text

        f = StreamTitleFilter()
        chunks = ["Here is your response.\n\n<", "suggested_", "title>Weekly Team ", "Sync</suggested_title>"]
        out = []
        for c in chunks:
            out.append(f.process_chunk(c))
        rem, title = f.finalize()
        out.append(rem)

        streamed_text = "".join(out)
        self.assertEqual(streamed_text, "Here is your response.\n\n")
        self.assertEqual(title, "Weekly Team Sync")

    def test_extract_title_from_text(self):
        from agent.llm.titles import extract_title_from_text

        text = "Hello! Let me help you.\n\n<suggested_title>Calendar Assistance</suggested_title>"
        clean_text, title = extract_title_from_text(text)
        self.assertEqual(clean_text, "Hello! Let me help you.")
        self.assertEqual(title, "Calendar Assistance")

    def test_extract_title_unclosed_tag(self):
        from agent.llm.titles import extract_title_from_text

        text = "Here is the summary.\n\n<suggested_title>Executive Summary"
        clean_text, title = extract_title_from_text(text)
        self.assertEqual(clean_text, "Here is the summary.")
        self.assertEqual(title, "Executive Summary")


class EnvelopeAndReferenceDetectorTests(TestCase):
    def test_strip_message_envelope(self):
        from agent.routing.reference_detector import strip_message_envelope

        raw1 = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"
        self.assertEqual(strip_message_envelope(raw1), "search my unread emails")

        raw2 = "Date: 2026-09-13 13:49:00, Alice: send it to Bob"
        self.assertEqual(strip_message_envelope(raw2), "send it to Bob")

        plain = "what tools are available"
        self.assertEqual(strip_message_envelope(plain), "what tools are available")

    def test_has_conversational_reference_with_envelope(self):
        from agent.routing.reference_detector import has_conversational_reference

        # With envelope, should still detect pronoun "it" correctly
        raw_ref = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: forward it to Sarah"
        self.assertTrue(has_conversational_reference(raw_ref))

        # With envelope, standalone query should NOT have reference
        raw_standalone = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"
        self.assertFalse(has_conversational_reference(raw_standalone))


class MultiTurnRoutingTests(TestCase):
    def test_standalone_multi_turn_bypasses_query_generator(self):
        history = [
            HumanMessage(content="What operations tasks can you assist me with?"),
            AIMessage(content="I can assist with Gmail, Calendar, Docs, Sheets, and Slack."),
            HumanMessage(content="Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"),
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        result = route_intent(history, available_domains=available_domains)
        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["intent"], "email.search")
        self.assertIsNone(result["call_metrics"], "Standalone query in multi-turn must bypass Call #1!")

    @patch("agent.routing.intent_router.generate_routing_query")
    def test_reference_multi_turn_triggers_query_generator(self, mock_query_gen):
        mock_query_gen.return_value = {
            "type": "SINGLE",
            "query": "Forward the Q3 Budget email to Sarah",
            "metrics": {
                "name": "Query Rewrite (Call #1)",
                "model": "llama-3.1-8b-instant",
                "input_tokens": 120,
                "output_tokens": 15,
                "total_tokens": 135,
                "cached_tokens": 0,
                "latency_ms": 150.0,
            },
        }
        history = [
            HumanMessage(content="Search for emails from Alex"),
            AIMessage(content="Found email from Alex: Subject: Q3 Budget."),
            HumanMessage(content="Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: forward it to Sarah"),
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        result = route_intent(history, available_domains=available_domains)
        self.assertEqual(result["domain"], "email")
        mock_query_gen.assert_called_once()
        self.assertIsNotNone(result["call_metrics"], "Pronoun 'it' must trigger Call #1 rewrite!")
        self.assertEqual(result["call_metrics"]["name"], "Query Rewrite (Call #1)")


class MetricsCollectorTests(TestCase):
    def test_extract_call_metrics_structure(self):
        from agent.metrics import extract_call_metrics, CallMetrics

        mock_response = AIMessage(
            content="Hello world",
            usage_metadata={"input_tokens": 100, "output_tokens": 25, "total_tokens": 125},
        )
        metrics = extract_call_metrics(
            response=mock_response,
            step_name="Test Call",
            latency_ms=250.5,
            model_name="openai/gpt-oss-120b",
        )
        self.assertEqual(metrics["name"], "Test Call")
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 25)
        self.assertEqual(metrics["total_tokens"], 125)
        self.assertEqual(metrics["latency_ms"], 250.5)
        self.assertEqual(metrics["model"], "openai/gpt-oss-120b")

    def test_aggregate_turn_metrics(self):
        from agent.metrics import aggregate_turn_metrics, CallMetrics

        call1: CallMetrics = {
            "name": "Query Generator (Call #1)",
            "model": "llama-3.1-8b-instant",
            "input_tokens": 150,
            "output_tokens": 30,
            "total_tokens": 180,
            "cached_tokens": 0,
            "latency_ms": 300,
        }
        call2: CallMetrics = {
            "name": "Email Agent (Call #2)",
            "model": "openai/gpt-oss-120b",
            "input_tokens": 800,
            "output_tokens": 120,
            "total_tokens": 920,
            "cached_tokens": 50,
            "latency_ms": 1200,
        }
        import time
        start_time = time.perf_counter() - 1.5
        turn_metrics = aggregate_turn_metrics([call1, call2], start_time=start_time)

        self.assertEqual(turn_metrics["total_tokens"], 1100)
        self.assertEqual(turn_metrics["input_tokens"], 950)
        self.assertEqual(turn_metrics["output_tokens"], 150)
        self.assertEqual(turn_metrics["cached_tokens"], 50)
        self.assertEqual(turn_metrics["llm_calls"], 2)
        self.assertEqual(len(turn_metrics["breakdown"]), 2)


