from unittest.mock import patch, MagicMock
from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.routing.reference_detector import (
    extract_action_directive,
    has_conversational_reference,
    detect_explicit_domains,
    is_domain_ambiguous,
)
from agent.routing.intent_router import route_intent
from agent.graph.state import _merge_metrics, AgentState
from agent.graph.approval import approval_node
from agent.llm.prompts import GENERAL_SYSTEM_PROMPT, BASE_SYSTEM_PROMPT


class DirectiveAndReferenceEdgeCases(TestCase):
    def test_closing_directive_extracted_from_large_payload(self):
        # Emulate user pasting a large report or email draft followed by a closing directive
        large_body = (
            "Here is the weekly financial overview for Q3 2026. Revenue grew by 14% year-over-year. "
            "Operating expenses were down 3% due to optimizations in cloud infrastructure. "
            "Customer acquisition costs dropped to $42 per qualified lead. "
        ) * 15  # ~600 words
        full_query = f"{large_body}\n\nsend it to shifa quick"

        directive = extract_action_directive(full_query)
        self.assertIsNotNone(directive)
        self.assertIn("send it to shifa quick", directive.lower())
        self.assertTrue(has_conversational_reference(full_query))

    def test_email_closing_directive_with_email_address(self):
        large_text = "Project update notes and deployment checklists for next Tuesday's release. " * 20
        full_query = f"{large_text}\nEmail this to john.doe@example.com"

        directive = extract_action_directive(full_query)
        self.assertIsNotNone(directive)
        self.assertIn("john.doe@example.com", directive)

        domains = detect_explicit_domains(full_query)
        self.assertIn("email", domains)

    def test_slack_closing_directive(self):
        large_text = "System alert: Memory utilization exceeded 85% on node worker-03. " * 10
        full_query = f"{large_text}\nsend this to #dev-ops on slack"

        domains = detect_explicit_domains(full_query)
        self.assertIn("slack", domains)
        self.assertTrue(has_conversational_reference(full_query))

    def test_minimal_and_short_inputs(self):
        # Edge cases where user provides minimal inputs (1-3 words)
        short_references = [
            "send it",
            "dispatch it",
            "email that",
            "yes do it",
            "sure go ahead",
            "send it now",
            "forward those",
        ]
        for query in short_references:
            with self.subTest(query=query):
                self.assertTrue(has_conversational_reference(query))

    def test_standalone_non_referential_queries(self):
        non_references = [
            "check latest email from arsalan",
            "what meetings are on my schedule today",
            "draft an announcement for the product launch",
        ]
        for query in non_references:
            with self.subTest(query=query):
                self.assertFalse(has_conversational_reference(query))

    def test_single_line_compound_prompt_not_truncated(self):
        # A single line prompt over 100 characters must not be sliced by directive extraction
        query = "check my calander for tommorow and tell my availbility to omer via mial send mail here omer.farooque@yahoo.com)"
        directive = extract_action_directive(query)
        self.assertEqual(directive, query)

    def test_typo_domain_detection(self):
        # Check that common typos like calander, calender, mial, availbility are mapped properly
        query = "check my calander for tommorow and tell my availbility to omer via mial send mail here omer.farooque@yahoo.com)"
        domains = detect_explicit_domains(query)
        self.assertIn("calendar", domains)
        self.assertIn("email", domains)


class IntentRouterPlanQueueTests(TestCase):
    def test_plan_queue_bypasses_query_rewrite_when_steps_pending(self):
        # When a plan exists with pending steps, route_intent should immediately return the step's domain
        plan = [
            {"domain": "docs", "status": "completed", "action": "create summary document"},
            {"domain": "email", "status": "pending", "action": "email document to team"},
            {"domain": "slack", "status": "pending", "action": "notify #announcements"},
        ]
        decision = route_intent(
            message="continue",
            available_domains={"email", "docs", "slack"},
            plan=plan,
        )
        self.assertEqual(decision["domain"], "email")
        self.assertEqual(decision["status"], "confident")
        self.assertIsNone(decision["call_metrics"])

    def test_explicit_domain_prioritized_over_general(self):
        # A message with explicit email domain shouldn't fallback to general
        decision = route_intent(
            message="send an email to test@example.com with subject Hi",
            available_domains={"email", "slack", "calendar"},
        )
        self.assertEqual(decision["domain"], "email")


class MetricsTurnIsolationTests(TestCase):
    def test_reset_signal_clears_prior_turn_metrics(self):
        # Prior turn accumulated metrics from turns 1-20
        old_metrics = [
            {"name": "Tool Call 1", "total_tokens": 12000, "input_tokens": 10000, "output_tokens": 2000},
            {"name": "Tool Call 2", "total_tokens": 15000, "input_tokens": 13000, "output_tokens": 2000},
        ]
        # Current turn starts with reset signal injected by runner
        new_turn_metrics = [
            {"__reset__": True},
            {"name": "Email Agent", "total_tokens": 850, "input_tokens": 700, "output_tokens": 150},
        ]
        merged = _merge_metrics(old_metrics, new_turn_metrics)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "Email Agent")
        self.assertEqual(merged[0]["total_tokens"], 850)

    def test_accumulates_within_same_turn_without_reset(self):
        turn_metrics_1 = [{"name": "Step 1", "total_tokens": 400}]
        turn_metrics_2 = [{"name": "Step 2", "total_tokens": 350}]
        merged = _merge_metrics(turn_metrics_1, turn_metrics_2)
        self.assertEqual(len(merged), 2)
        self.assertEqual(sum(m["total_tokens"] for m in merged), 750)


class ApprovalNodeModificationTests(TestCase):
    @patch("agent.graph.approval.interrupt")
    def test_approval_node_applies_modified_args(self, mock_interrupt):
        # When user approves with modified arguments, tool_call args are updated
        mock_interrupt.return_value = {
            "approved": True,
            "modified_args": {
                "to": ["updated_recipient@example.com"],
                "subject": "Updated Subject",
                "body": "Updated Body Content",
            },
        }

        tool_call = {
            "id": "tc_123",
            "name": "send_gmail_message",
            "args": {
                "to": "original@example.com",
                "subject": "Original Subject",
                "body": "Original Body",
            },
        }
        ai_message = AIMessage(content="", tool_calls=[tool_call])
        state = {
            "messages": [
                HumanMessage(content="Send email to original@example.com"),
                ai_message,
            ],
            "approved": False,
        }

        result = approval_node(state)
        self.assertTrue(result["approved"])
        # Verify the tool call in messages was patched with modified args
        patched_tc = result["messages"][-1].tool_calls[0]
        self.assertEqual(patched_tc["args"]["to"], ["updated_recipient@example.com"])
        self.assertEqual(patched_tc["args"]["subject"], "Updated Subject")
        self.assertEqual(patched_tc["args"]["body"], "Updated Body Content")

    @patch("agent.graph.approval.interrupt")
    def test_approval_node_handles_instruction_revision(self, mock_interrupt):
        # When user rejects with an instruction, state captures instruction and rejects approval
        mock_interrupt.return_value = {
            "approved": False,
            "instruction": "Please make the tone more formal and add a signature.",
        }

        tool_call = {
            "id": "tc_456",
            "name": "slack_send_message",
            "args": {"channel": "#general", "message": "hey team"},
        }
        ai_message = AIMessage(content="", tool_calls=[tool_call])
        state = {
            "messages": [
                HumanMessage(content="Send message to team"),
                ai_message,
            ],
            "approved": False,
        }

        result = approval_node(state)
        self.assertFalse(result["approved"])
        # Verify a human feedback message was added for the agent to revise
        last_message = result["messages"][-1]
        self.assertIsInstance(last_message, HumanMessage)
        self.assertIn("Revisions requested:", last_message.content)
        self.assertIn("more formal", last_message.content)


class GeneralAgentSystemPromptTests(TestCase):
    def test_general_system_prompt_forbids_tool_calls(self):
        # Ensure GENERAL_SYSTEM_PROMPT explicitly informs the model it does not emit tool calls
        self.assertIn("Do not attempt to format or emit tool or function calls", GENERAL_SYSTEM_PROMPT)
        self.assertIn("conversational responses", GENERAL_SYSTEM_PROMPT)
