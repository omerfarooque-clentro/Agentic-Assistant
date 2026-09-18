"""Comprehensive tests for the Multi-Agent Workflow Engine:
planning, cross-domain transition loop, and multi-step execution.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from accounts.models import User
from conversations.models import Message, Thread
from agent.graph.nodes import advance_plan_node, agent_node, nlp_node, scoped_should_continue
from agent.graph.state import AgentState
from agent.routing.query_generator import ParsedPlanStep, generate_routing_query
from agent.routing.reference_detector import is_compound_multi_domain
from agent.routing.intent_router import route_intent
from agent.streaming import event_stream, approval_event_stream


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

    def test_agent_node_marks_final_plan_step_completed(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Email confirmation sent to omer.farooque@yahoo.com.")

        state: AgentState = {
            "messages": [HumanMessage(content="send link")],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "in_progress", "result_summary": None},
            ],
            "current_step_index": 1,
            "domain": "email",
        }
        output = agent_node(state, mock_llm, domain="email")
        self.assertIn("plan", output)
        self.assertEqual(output["plan"][1]["status"], "completed")
        self.assertIn("Email confirmation sent", output["plan"][1]["result_summary"])

    def test_route_intent_does_not_hijack_when_all_steps_completed(self):
        plan = [
            {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
            {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "completed", "result_summary": "Sent"},
        ]
        # Query is a new search request; should NOT route to email or calendar
        result = route_intent("search web tell me what's the weather hyderabad pakistan", available_domains={"calendar", "email", "research"}, plan=plan)
        self.assertEqual(result["domain"], "research")
        self.assertEqual(result["intent"], "research.search")

    def test_route_intent_prioritizes_active_plan(self):
        plan = [
            {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
            {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "in_progress", "result_summary": None},
        ]
        result = route_intent("continue execution", available_domains={"calendar", "email", "research"}, plan=plan)
        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["intent"], "email.send")

    def test_nlp_node_resets_completed_plan_on_fresh_turn(self):
        state: AgentState = {
            "messages": [HumanMessage(content="search web for weather in hyderabad")],
            "plan": [
                {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
                {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "completed", "result_summary": "Sent"},
            ],
            "current_step_index": 1,
        }
        output = nlp_node(state, available_domains={"calendar", "email", "research"})
        self.assertEqual(output["domain"], "research")
        self.assertEqual(output["plan"], [])
        self.assertEqual(output["current_step_index"], 0)

    def test_route_intent_advances_to_next_plan_step_without_hijack_by_ai_message(self):
        plan = [
            {"id": 1, "domain": "calendar", "intent": "calendar.create", "description": "Schedule meeting", "status": "completed", "result_summary": "Done"},
            {"id": 2, "domain": "email", "intent": "email.send", "description": "Send link", "status": "in_progress", "result_summary": None},
        ]
        # Previous agent response mentioned calendar; it must NOT hijack the plan to calendar!
        messages = [
            HumanMessage(content="Schedule interview and email link"),
            AIMessage(content="I have scheduled the technical interview on your Google Calendar."),
        ]
        result = route_intent(messages, available_domains={"calendar", "email"}, plan=plan)
        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["intent"], "email.send")


@tool
def manage_event(summary: str, start_time: str = ""):
    """Create or update a calendar event."""
    return f"Event created: {summary}"


@tool
def send_gmail_message(recipient: str, subject: str = "", body: str = ""):
    """Send an email message via Gmail."""
    return f"Email sent to {recipient}"


@tool
def slack_send_message(channel: str, message: str = ""):
    """Post a message to a Slack channel."""
    return f"Slack message posted to {channel}"


class MultiAgentEndToEndExecutionTests(TransactionTestCase):
    """End-to-end multi-step workflow tests ensuring responses are delivered to FE without silent stalls."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="test_multi_agent@example.com",
            username="test_multi_agent",
            password="TestPassword123!",
        )
        self.thread = Thread.objects.create(
            user=self.user,
            name="Multi-Step Execution Thread",
        )
        self.tools = {
            "calendar": [manage_event],
            "email": [send_gmail_message],
            "slack": [slack_send_message],
        }

    async def test_two_step_workflow_completes_and_feeds_response_to_fe(self):
        """Verify 2-step workflow executes step 1 -> step 2 and delivers final response to FE."""
        mock_plan = {
            "type": "MULTI",
            "query": "Schedule meeting and email confirmation",
            "steps": [
                {"domain": "calendar", "query": "schedule meeting tomorrow at 9pm"},
                {"domain": "email", "query": "send email to omer@example.com"},
            ],
            "metrics": None,
        }
        step_responses = [
            AIMessage(content="Step 1: Calendar meeting booked for tomorrow at 9:00 PM."),
            AIMessage(content="Step 2: Confirmation email sent to omer@example.com."),
        ]
        call_count = 0

        def mock_invoke(*args, **kwargs):
            nonlocal call_count
            res = step_responses[min(call_count, len(step_responses) - 1)]
            call_count += 1
            return res

        mock_bound = MagicMock()
        mock_bound.invoke = mock_invoke
        mock_bound.model_name = "test-model"

        mem = MemorySaver()
        prompt = "Schedule meeting tomorrow at 9pm and send email to omer@example.com"
        with patch("agent.graph.builder.memory", mem), \
             patch("agent.runner.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.runner.get_user_tools", return_value=self.tools), \
             patch("agent.routing.intent_router.generate_routing_query", return_value=mock_plan), \
             patch("agent.graph.builder.bind_tools_with_fallback", return_value=mock_bound):

            events = []
            async for sse in event_stream(prompt, self.thread, self.user):
                events.append(sse)

            completed_events = [
                json.loads(e.strip()[5:]) for e in events if '"type": "completed"' in e
            ]
            error_events = [
                json.loads(e.strip()[5:]) for e in events if '"type": "error"' in e
            ]

            self.assertEqual(len(error_events), 0, f"Workflow failed with error: {error_events}")
            self.assertEqual(len(completed_events), 1, "Expected exactly 1 completed event delivered to FE")

            completed_payload = completed_events[0]
            response_text = completed_payload.get("response", "")

            # Confirm both steps' outputs are included and not overwritten
            self.assertIn("Step 1: Calendar meeting booked", response_text)
            self.assertIn("Step 2: Confirmation email sent", response_text)

            # Confirm agent message was persisted in the database
            db_msg = await Message.objects.filter(thread=self.thread, role="agent").alast()
            self.assertIsNotNone(db_msg)
            self.assertIn("Step 1: Calendar meeting booked", db_msg.content)
            self.assertIn("Step 2: Confirmation email sent", db_msg.content)

    async def test_three_step_workflow_completes_without_recursion_limit_failure(self):
        """Verify 3-step workflow (Calendar -> Email -> Slack) runs through all steps without GraphRecursionError."""
        mock_plan = {
            "type": "MULTI",
            "query": "Schedule meeting, email client, and notify slack",
            "steps": [
                {"domain": "calendar", "query": "schedule meeting tomorrow at 9pm"},
                {"domain": "email", "query": "send email to omer@example.com"},
                {"domain": "slack", "query": "notify #announcements on slack"},
            ],
            "metrics": None,
        }
        step_responses = [
            AIMessage(content="Step 1: Scheduled meeting for tomorrow at 9:00 PM."),
            AIMessage(content="Step 2: Sent confirmation email to omer@example.com."),
            AIMessage(content="Step 3: Notified team on Slack #announcements."),
        ]
        call_count = 0

        def mock_invoke(*args, **kwargs):
            nonlocal call_count
            res = step_responses[min(call_count, len(step_responses) - 1)]
            call_count += 1
            return res

        mock_bound = MagicMock()
        mock_bound.invoke = mock_invoke
        mock_bound.model_name = "test-model"

        mem = MemorySaver()
        prompt = "Schedule meeting tomorrow at 9pm and send email to omer@example.com and notify on slack"
        with patch("agent.graph.builder.memory", mem), \
             patch("agent.runner.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.runner.get_user_tools", return_value=self.tools), \
             patch("agent.routing.intent_router.generate_routing_query", return_value=mock_plan), \
             patch("agent.graph.builder.bind_tools_with_fallback", return_value=mock_bound):

            events = []
            async for sse in event_stream(prompt, self.thread, self.user):
                events.append(sse)

            completed_events = [
                json.loads(e.strip()[5:]) for e in events if '"type": "completed"' in e
            ]
            error_events = [
                json.loads(e.strip()[5:]) for e in events if '"type": "error"' in e
            ]

            self.assertEqual(len(error_events), 0, f"Workflow hit error: {error_events}")
            self.assertEqual(len(completed_events), 1, "Terminal completed event must be yielded to FE")

            response_text = completed_events[0].get("response", "")
            self.assertIn("Step 1: Scheduled meeting", response_text)
            self.assertIn("Step 2: Sent confirmation email", response_text)
            self.assertIn("Step 3: Notified team on Slack", response_text)

    async def test_multi_step_workflow_with_approval_interrupt_and_resumption(self):
        """Verify multi-step workflow pauses for approval on gated tools and resumes to complete next steps."""
        mock_plan = {
            "type": "MULTI",
            "query": "Schedule meeting and email invite",
            "steps": [
                {"domain": "calendar", "query": "schedule meeting tomorrow at 9pm"},
                {"domain": "email", "query": "send email to omer@example.com"},
            ],
            "metrics": None,
        }
        step_responses = [
            # Turn 1: Calendar agent generates approval-gated tool call
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "manage_event",
                    "args": {"summary": "Technical Interview"},
                    "id": "call_cal_1",
                    "type": "tool_call",
                }],
            ),
            # Turn 2: Calendar agent receives tool result and confirms
            AIMessage(content="Calendar event booked."),
            # Step 2: Email agent executes and confirms
            AIMessage(content="Confirmation email dispatched to omer@example.com."),
        ]
        call_count = 0

        def mock_invoke(*args, **kwargs):
            nonlocal call_count
            res = step_responses[min(call_count, len(step_responses) - 1)]
            call_count += 1
            return res

        mock_bound = MagicMock()
        mock_bound.invoke = mock_invoke
        mock_bound.model_name = "test-model"

        mem = MemorySaver()
        prompt = "Schedule meeting tomorrow at 9pm and send email to omer@example.com"
        with patch("agent.graph.builder.memory", mem), \
             patch("agent.runner.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.runner.get_user_tools", return_value=self.tools), \
             patch("agent.streaming.get_user_tools", return_value=self.tools), \
             patch("agent.routing.intent_router.generate_routing_query", return_value=mock_plan), \
             patch("agent.graph.builder.bind_tools_with_fallback", return_value=mock_bound):

            # Turn 1: Chat request triggers approval interrupt
            turn1_events = []
            async for sse in event_stream(prompt, self.thread, self.user):
                turn1_events.append(sse)

            approval_event = next(
                (json.loads(e.strip()[5:]) for e in turn1_events if '"type": "approval_required"' in e),
                None,
            )
            self.assertIsNotNone(approval_event, "Must yield approval_required event to FE")
            self.assertEqual(approval_event["approval"]["domain"], "calendar")
            self.assertEqual(approval_event["approval"]["tool_name"], "manage_event")

            # Turn 2: User approves Step 1 -> resumes graph, executes tool, advances to Step 2
            config = {
                "configurable": {"thread_id": str(self.thread.id)},
                "recursion_limit": 25,
            }
            turn2_events = []
            async for sse in approval_event_stream(
                approval=approval_event["approval"],
                thread=self.thread,
                user=self.user,
                config=config,
                approved=True,
                modified_args=None,
                instruction=None,
            ):
                turn2_events.append(sse)

            completed_event = next(
                (json.loads(e.strip()[5:]) for e in turn2_events if '"type": "completed"' in e),
                None,
            )
            self.assertIsNotNone(completed_event, "Resume stream must deliver completed event to FE")
            self.assertIn("Confirmation email dispatched", completed_event["result"])

    async def test_multi_step_workflow_error_yields_explicit_error_event(self):
        """Verify unexpected runtime errors in multi-step flow yield an explicit error event instead of hanging."""
        mock_plan = {
            "type": "MULTI",
            "query": "Schedule meeting and email",
            "steps": [
                {"domain": "calendar", "query": "schedule meeting tomorrow at 9pm"},
                {"domain": "email", "query": "send email to omer@example.com"},
            ],
            "metrics": None,
        }
        mock_bound = MagicMock()
        mock_bound.invoke.side_effect = RuntimeError("External API gateway timeout")
        mock_bound.model_name = "test-model"

        mem = MemorySaver()
        prompt = "Schedule meeting tomorrow at 9pm and send email to omer@example.com"
        with patch("agent.graph.builder.memory", mem), \
             patch("agent.runner.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock), \
             patch("agent.runner.get_user_tools", return_value=self.tools), \
             patch("agent.routing.intent_router.generate_routing_query", return_value=mock_plan), \
             patch("agent.graph.builder.bind_tools_with_fallback", return_value=mock_bound):

            events = []
            async for sse in event_stream(prompt, self.thread, self.user):
                events.append(sse)

            error_event = next(
                (json.loads(e.strip()[5:]) for e in events if '"type": "error"' in e),
                None,
            )
            self.assertIsNotNone(error_event, "Error event must be emitted so FE does not hang silently")
            self.assertIn("External API gateway timeout", error_event["message"])

            # Verify error message is recorded in the DB
            db_msg = await Message.objects.filter(thread=self.thread, role="agent").alast()
            self.assertIsNotNone(db_msg)
            self.assertIn("External API gateway timeout", db_msg.content)
