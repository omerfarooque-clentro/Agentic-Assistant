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

