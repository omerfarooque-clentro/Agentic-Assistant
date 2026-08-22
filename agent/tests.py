from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.llm.messages import MAX_TOOL_MESSAGE_CHARS, messages_for_llm


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
