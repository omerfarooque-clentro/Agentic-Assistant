"""Comprehensive tests for the Multi-Agent Workflow Engine:
planning, cross-domain transition loop, and multi-step execution.
"""

from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph.nodes import advance_plan_node, nlp_node, scoped_should_continue
from agent.graph.state import AgentState
from agent.routing.query_generator import ParsedPlanStep, generate_routing_query
from agent.routing.reference_detector import is_compound_multi_domain
from agent.routing.intent_router import route_intent


class MultiAgentPlanningTests(SimpleTestCase):
    def test_is_compound_multi_domain_detection(self):
        # Calendar + Email
        text1 = "Schedule a meeting tomorrow at 9 PM and send the link to omer.farooque@yahoo.com"
        self.assertTrue(is_compound_multi_domain(text1, {"calendar", "email"}))

        # Docs + Slack
        text2 = "Create a project summary doc and share it on slack in #announcements"
        self.assertTrue(is_compound_multi_domain(text2, {"docs", "slack"}))

        # Single domain (only calendar)
        text3 = "Schedule a meeting tomorrow at 9 PM for project review"
        self.assertFalse(is_compound_multi_domain(text3, {"calendar", "email"}))

        # Single domain (only email)
        text4 = "Send an email to test@example.com with subject Meeting Notes"
        self.assertFalse(is_compound_multi_domain(text4, {"calendar", "email"}))

        # Domain not available
        text5 = "Schedule a meeting and share it on slack"
        self.assertFalse(is_compound_multi_domain(text5, {"calendar"}))  # slack not available

    @patch("agent.routing.query_generator.llm")
    def test_generate_routing_query_parses_multi_step_plan(self, mock_llm):
        mock_response = MagicMock()
        mock_response.content = (
            "PLAN:\n"
            "1. [calendar] schedule technical interview tomorrow at 9:00 PM\n"
            "2. [email] send interview link to omer.farooque@yahoo.com"
        )
        mock_llm.invoke.return_value = mock_response

        result = generate_routing_query(["Schedule interview tomorrow 9pm and email link to omer.farooque@yahoo.com"])
        self.assertEqual(result["type"], "MULTI")
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["domain"], "calendar")
        self.assertEqual(result["steps"][0]["query"], "schedule technical interview tomorrow at 9:00 PM")
        self.assertEqual(result["steps"][1]["domain"], "email")
        self.assertEqual(result["steps"][1]["query"], "send interview link to omer.farooque@yahoo.com")

    @patch("agent.routing.query_generator.llm")
    def test_generate_routing_query_single_task_fallback(self, mock_llm):
        mock_response = MagicMock()
        mock_response.content = "QUERY: schedule technical interview tomorrow at 9:00 PM"
        mock_llm.invoke.return_value = mock_response

        result = generate_routing_query(["Schedule interview tomorrow 9pm"])
        self.assertEqual(result["type"], "SINGLE")
        self.assertEqual(result["query"], "schedule technical interview tomorrow at 9:00 PM")
        self.assertEqual(result["steps"], [])


class MultiAgentGraphProgressionTests(SimpleTestCase):
    def test_scoped_should_continue_routes_to_advance_plan_when_steps_remain(self):
        state: AgentState = {
            "messages": [AIMessage(content="I have scheduled the technical interview for tomorrow at 9:00 PM.")],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "in_progress", "result_summary": None},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "pending", "result_summary": None},
            ],
            "current_step_index": 0,
        }
        # No pending tool calls in last message, but step 2 is pending
        decision = scoped_should_continue(state, "calendar")
        self.assertEqual(decision, "advance_plan")

    def test_scoped_should_continue_routes_to_end_when_all_steps_completed(self):
        state: AgentState = {
            "messages": [AIMessage(content="Email sent to omer.farooque@yahoo.com.")],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Created"},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "in_progress", "result_summary": None},
            ],
            "current_step_index": 1,
        }
        decision = scoped_should_continue(state, "email")
        self.assertEqual(decision, "end")

    def test_scoped_should_continue_still_gates_approval_even_in_plan(self):
        state: AgentState = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "manage_event", "args": {"summary": "Interview"}, "id": "call_1", "type": "tool_call"}],
                )
            ],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "in_progress", "result_summary": None},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "pending", "result_summary": None},
            ],
            "current_step_index": 0,
        }
        decision = scoped_should_continue(state, "calendar")
        self.assertEqual(decision, "approval")

    def test_advance_plan_node_transitions_steps_correctly(self):
        state: AgentState = {
            "messages": [
                AIMessage(content="Successfully scheduled event 'Technical Interview'. Meet link: https://meet.google.com/xyz")
            ],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "in_progress", "result_summary": None},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "pending", "result_summary": None},
            ],
            "current_step_index": 0,
        }
        update = advance_plan_node(state)
        updated_plan = update["plan"]

        self.assertEqual(updated_plan[0]["status"], "completed")
        self.assertIn("https://meet.google.com/xyz", updated_plan[0]["result_summary"])
        self.assertEqual(updated_plan[1]["status"], "in_progress")
        self.assertEqual(update["current_step_index"], 1)

    @patch("agent.routing.intent_router.generate_routing_query")
    def test_route_intent_initializes_multi_agent_plan(self, mock_query_gen):
        mock_query_gen.return_value = {
            "type": "MULTI",
            "query": "Schedule technical interview",
            "steps": [
                {"domain": "calendar", "query": "schedule technical interview tomorrow at 9:00 PM"},
                {"domain": "email", "query": "send interview link to omer.farooque@yahoo.com"},
            ],
            "metrics": None,
        }

        user_prompt = "Schedule technical interview tomorrow at 9:00 PM and send link to omer.farooque@yahoo.com"
        result = route_intent(user_prompt, available_domains={"calendar", "email"})

        self.assertEqual(result["domain"], "calendar")
        self.assertEqual(result["intent"], "calendar.create")
        self.assertIn("plan", result)
        self.assertEqual(len(result["plan"]), 2)
        self.assertEqual(result["plan"][0]["domain"], "calendar")
        self.assertEqual(result["plan"][0]["status"], "in_progress")
        self.assertEqual(result["plan"][1]["domain"], "email")
        self.assertEqual(result["plan"][1]["status"], "pending")

    def test_route_intent_consumes_active_plan_in_subsequent_steps(self):
        plan = [
            {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
            {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "in_progress", "result_summary": None},
        ]
        result = route_intent("continue", available_domains={"calendar", "email"}, plan=plan)

        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["intent"], "email.send")
        self.assertEqual(result["status"], "confident")
