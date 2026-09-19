from django.test import TestCase
from langchain_core.messages import AIMessage, ToolMessage

from agent.cards import render_cards


class MockApproval:
    def __init__(self, domain="general"):
        self.domain = domain


class ApprovalRegressionTests(TestCase):
    def test_import_and_render_approval_cards(self):
        approval = MockApproval(domain="email")
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "send_gmail_message", "args": {"to": "test@example.com", "subject": "Hi"}}],
            )
        ]
        card = render_cards(approval, messages, approved=True)
        self.assertEqual(card["domain"], "email")
        self.assertEqual(card["status"], "completed")
        self.assertEqual(card["args"].get("to"), "test@example.com")
        self.assertNotIn("kind", card)

    def test_render_cancelled_approval_card(self):
        approval = MockApproval(domain="calendar")
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"id": "call_2", "name": "manage_event", "args": {"summary": "Sync"}}],
            )
        ]
        card = render_cards(approval, messages, approved=False)
        self.assertEqual(card["domain"], "calendar")
        self.assertEqual(card["status"], "cancelled")
