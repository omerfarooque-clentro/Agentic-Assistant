from django.test import SimpleTestCase, TestCase
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.views import (
    LoginView,
    RegistrationView,
    forgot_password_view,
    verify_otp_view,
    reset_password_view,
    format_user_agent_message,
)
from agent.cards import render_cards
from core.serializers import (
    AgentChatSerializer,
    LoginSerializer,
    RegisterationSerializer,
    ForgotPasswordSerializer,
    VerifyOTPSerializer,
    ResetPasswordSerializer,
)
from accounts.models import User
from accounts.utils import (
    generate_recovery_otp,
    hash_recovery_otp,
    normalize_otp,
    verify_recovery_otp,
)


from django.test import TransactionTestCase, AsyncClient


import json
async def parse_sse_final(response):
    """Consume the async SSE response and return the terminal event payload."""
    events = []

    async for chunk in response.streaming_content:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")

        for line in chunk.splitlines():
            line = line.strip()

            if not line.startswith("data:"):
                continue

            payload = line[len("data:") :].strip()

            if not payload:
                continue

            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue

    for event in reversed(events):
        if event.get("type") in ("completed", "approval_required", "error"):
            return event

    return events[-1] if events else {}


class AuthURLTests(SimpleTestCase):
    def test_registration_url_resolves(self):
        match = resolve("/registration/")
        self.assertEqual(match.func.view_class, RegistrationView)

    def test_login_url_resolves(self):
        match = resolve("/login/")
        self.assertEqual(match.func.view_class, LoginView)

    def test_forgot_password_url_resolves(self):
        match = resolve("/forgot-password/")
        self.assertEqual(match.func, forgot_password_view)

    def test_verify_otp_url_resolves(self):
        match = resolve("/verify-otp/")
        self.assertEqual(match.func, verify_otp_view)

    def test_reset_password_api_url_resolves(self):
        match = resolve("/reset-password-api/")
        self.assertEqual(match.func, reset_password_view)

    def test_tool_approval_url_resolves(self):
        match = resolve("/api/thread/123/tool-approval/")
        self.assertEqual(match.view_name, "approve-email")

    def test_delete_thread_url_resolves(self):
        match = resolve("/api/thread/123/delete/")
        self.assertEqual(match.view_name, "delete-thread")


class RecoveryOTPUtilsTests(SimpleTestCase):
    def test_generate_and_verify_otp(self):
        raw_otp = generate_recovery_otp()
        self.assertTrue(raw_otp.startswith("PO-"))
        hashed = hash_recovery_otp(raw_otp)

        # Exact match
        self.assertTrue(verify_recovery_otp(raw_otp, hashed))
        # Lowercase / unformatted match
        cleaned = raw_otp.lower().replace("-", "")
        self.assertTrue(verify_recovery_otp(cleaned, hashed))
        # Wrong OTP
        self.assertFalse(verify_recovery_otp("PO-WRONG-CODE-1234", hashed))

    def test_normalize_otp(self):
        self.assertEqual(normalize_otp("PO-ABCD-EFGH-JKMN"), "ABCDEFGHJKMN")
        self.assertEqual(normalize_otp("po-abcd-efgh-jkmn"), "ABCDEFGHJKMN")
        self.assertEqual(normalize_otp("  PO - 1234 - 5678  "), "12345678")


class AuthSerializerTests(TestCase):
    def test_login_serializer_accepts_valid_credentials(self):
        user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="StrongPass123"
        )

        serializer = LoginSerializer(data={
            "email": "alice@example.com",
            "password": "StrongPass123",
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["user"], user)

    def test_login_serializer_rejects_invalid_credentials(self):
        serializer = LoginSerializer(data={
            "email": "nope@example.com",
            "password": "wrongpass",
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_registration_serializer_generates_recovery_code(self):
        serializer = RegisterationSerializer(data={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "MySecretPassword123!",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()

        self.assertIsNotNone(user.otp_secret)
        self.assertTrue(user.check_password("MySecretPassword123!"))
        data = serializer.data
        self.assertIn("recovery_code", data)
        self.assertTrue(data["recovery_code"].startswith("PO-"))
        self.assertTrue(verify_recovery_otp(data["recovery_code"], user.otp_secret))


class AuthViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_login_post_returns_tokens(self):
        user = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="StrongPass123"
        )

        response = self.client.post("/login/", {
            "email": "bob@example.com",
            "password": "StrongPass123",
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(response.data["email"], user.email)

    def test_registration_post_returns_credentials(self):
        response = self.client.post("/registration/", {
            "username": "david",
            "email": "david@example.com",
            "password": "StrongPass123!",
        }, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["username"], "david")
        self.assertIn("recovery_code", response.data)
        self.assertTrue(response.data["recovery_code"].startswith("PO-"))

    def test_registration_duplicate_email_fails(self):
        User.objects.create_user(
            username="originaluser",
            email="duplicate@example.com",
            password="StrongPassword1!"
        )

        response = self.client.post("/registration/", {
            "username": "seconduser",
            "email": "duplicate@example.com",
            "password": "OtherPassword123!",
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_forgot_password_view(self):
        User.objects.create_user(
            username="emma",
            email="emma@example.com",
            password="StrongPass123"
        )

        # Existing user
        res = self.client.post("/api/auth/forgot-password/", {
            "email": "emma@example.com"
        }, format="json")
        self.assertEqual(res.status_code, 200)

        # Non-existing user
        res_fail = self.client.post("/api/auth/forgot-password/", {
            "email": "unknown@example.com"
        }, format="json")
        self.assertEqual(res_fail.status_code, 400)

    def test_verify_otp_view(self):
        raw_otp = generate_recovery_otp()
        User.objects.create_user(
            username="frank",
            email="frank@example.com",
            password="OldPassword123",
            otp_secret=hash_recovery_otp(raw_otp)
        )

        # Valid OTP
        res = self.client.post("/api/auth/verify-otp/", {
            "email": "frank@example.com",
            "otp": raw_otp
        }, format="json")
        self.assertEqual(res.status_code, 200)

        # Invalid OTP
        res_bad = self.client.post("/api/auth/verify-otp/", {
            "email": "frank@example.com",
            "otp": "PO-INVALID-CODE-0000"
        }, format="json")
        self.assertEqual(res_bad.status_code, 400)

    def test_reset_password_updates_password_and_generates_new_otp(self):
        initial_raw_otp = generate_recovery_otp()
        user = User.objects.create_user(
            username="grace",
            email="grace@example.com",
            password="OldPassword123!",
            otp_secret=hash_recovery_otp(initial_raw_otp)
        )

        # Reset password
        res = self.client.post("/api/auth/reset-password/", {
            "email": "grace@example.com",
            "otp": initial_raw_otp,
            "new_password": "BrandNewPassword123!",
            "confirm_password": "BrandNewPassword123!"
        }, format="json")

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("recovery_code", data)
        new_otp = data["recovery_code"]
        self.assertNotEqual(new_otp, initial_raw_otp)

        # Reload user from DB
        user.refresh_from_db()
        self.assertTrue(user.check_password("BrandNewPassword123!"))
        self.assertFalse(user.check_password("OldPassword123!"))

        # Verify new OTP works and old OTP is invalidated
        self.assertTrue(verify_recovery_otp(new_otp, user.otp_secret))
        self.assertFalse(verify_recovery_otp(initial_raw_otp, user.otp_secret))

    def test_in_app_reset_password_with_current_password(self):
        user = User.objects.create_user(
            username="helen",
            email="helen@example.com",
            password="OldPassword123!"
        )
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

        res = self.client.post("/api/auth/change-password/", {
            "current_password": "OldPassword123!",
            "new_password": "NewSecretPassword456!",
            "confirm_password": "NewSecretPassword456!"
        }, format="json")

        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewSecretPassword456!"))

    def test_in_app_reset_password_with_recovery_otp_and_rotation(self):
        initial_otp = generate_recovery_otp()
        user = User.objects.create_user(
            username="ian",
            email="ian@example.com",
            password="OldPassword123!",
            otp_secret=hash_recovery_otp(initial_otp)
        )
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

        res = self.client.post("/api/auth/change-password/", {
            "otp": initial_otp,
            "new_password": "NewPassword789!",
            "confirm_password": "NewPassword789!"
        }, format="json")

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("rotated_otp"))
        new_otp = data.get("recovery_code")
        self.assertIsNotNone(new_otp)
        self.assertNotEqual(new_otp, initial_otp)

        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPassword789!"))
        self.assertTrue(verify_recovery_otp(new_otp, user.otp_secret))
        self.assertFalse(verify_recovery_otp(initial_otp, user.otp_secret))

    def test_otp_generate_requires_current_password(self):
        old_otp = generate_recovery_otp()
        user = User.objects.create_user(
            username="julia",
            email="julia@example.com",
            password="MySecretPassword123!",
            otp_secret=hash_recovery_otp(old_otp)
        )
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

        # Wrong password
        res_bad = self.client.post("/api/auth/otp-generate/", {
            "password": "WrongPassword!"
        }, format="json")
        self.assertEqual(res_bad.status_code, 400)

        # Correct password
        res_ok = self.client.post("/api/auth/otp-generate/", {
            "password": "MySecretPassword123!"
        }, format="json")
        self.assertEqual(res_ok.status_code, 200)
        data = res_ok.json()
        new_otp = data.get("recovery_code")
        self.assertIsNotNone(new_otp)
        self.assertNotEqual(new_otp, old_otp)

        user.refresh_from_db()
        self.assertTrue(verify_recovery_otp(new_otp, user.otp_secret))
        self.assertFalse(verify_recovery_otp(old_otp, user.otp_secret))


class JWTAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="charlie",
            email="charlie@example.com",
            password="StrongPass123"
        )
        self.client = APIClient()

    def test_valid_jwt_is_authenticated(self):
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

        response = self.client.get("/api/list_thread/")
        self.assertEqual(response.status_code, 200)


class AgentChatSerializerTests(SimpleTestCase):
    def test_serializer_with_message_only_defaults_to_utc(self):
        serializer = AgentChatSerializer(data={"message": "Hello world"})
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["message"], "Hello world")
        self.assertEqual(serializer.validated_data.get("timezone"), "UTC")

    def test_serializer_with_explicit_timezone(self):
        serializer = AgentChatSerializer(data={"message": "Schedule a meeting", "timezone": "America/New_York"})
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["message"], "Schedule a meeting")
        self.assertEqual(serializer.validated_data["timezone"], "America/New_York")

    def test_serializer_missing_message_is_invalid(self):
        serializer = AgentChatSerializer(data={"timezone": "America/New_York"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("message", serializer.errors)


class FormatUserAgentMessageTests(SimpleTestCase):
    def test_format_with_valid_timezone(self):
        result = format_user_agent_message("Schedule a meeting tomorrow at 3pm", "alice", "America/New_York")
        self.assertIn("alice: Schedule a meeting tomorrow at 3pm", result)
        self.assertIn("(Timezone: America/New_York)", result)
        self.assertTrue(result.startswith("Date: "))

    def test_format_with_asia_karachi_timezone(self):
        result = format_user_agent_message("List my emails", "bob", "Asia/Karachi")
        self.assertIn("bob: List my emails", result)
        self.assertIn("(Timezone: Asia/Karachi)", result)
        self.assertTrue(result.startswith("Date: "))

    def test_format_with_invalid_timezone_falls_back_to_utc(self):
        result = format_user_agent_message("Hello", "charlie", "Invalid/Fake_Zone")
        self.assertIn("charlie: Hello", result)
        self.assertIn("(Timezone: UTC)", result)
        self.assertTrue(result.startswith("Date: "))

    def test_format_with_empty_or_none_timezone_falls_back_to_utc(self):
        result_none = format_user_agent_message("Hello", "charlie", None)
        self.assertIn("(Timezone: UTC)", result_none)

        result_empty = format_user_agent_message("Hello", "charlie", "   ")
        self.assertIn("(Timezone: UTC)", result_empty)


from unittest.mock import patch, AsyncMock, MagicMock
from django.test import TestCase, AsyncClient
from rest_framework_simplejwt.tokens import RefreshToken
from langchain_core.messages import AIMessage
from conversations.models import Thread, Message, Approval

class ToolApprovalViewTests(TransactionTestCase):
    def setUp(self):
        # Sync setUp runs normally before each async test
        self.user = User.objects.create_user(
            username="approval_user",
            email="approval@example.com",
            password="StrongPassword123!"
        )
        self.async_client = AsyncClient()
        token = RefreshToken.for_user(self.user)
        self.auth_headers = {"Authorization": f"Bearer {token.access_token}"}
        self.thread = Thread.objects.create(user=self.user, name="Meeting Thread")
        self.user_msg = Message.objects.create(thread=self.thread, role="user", content="Schedule meeting")

    async def test_unauthenticated_request_rejected(self):
        unauth_client = AsyncClient()
        response = await unauth_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    async def test_method_not_allowed(self):
        response = await self.async_client.get(
            f"/api/thread/{self.thread.id}/tool-approval/",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 405)

    async def test_nonexistent_thread_returns_404(self):
        response = await self.async_client.post(
            "/api/thread/999999/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 404)

    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_successful_tool_approval(self, mock_create_graph, mock_get_tools, mock_ensure_cp):
        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [AIMessage(content="Event scheduled successfully!")],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["type"], "completed")
        self.assertEqual(data["result"], "Event scheduled successfully!")
        self.assertEqual(data["thread_id"], self.thread.id)

        # Async DB assertion
        agent_msg = await Message.objects.filter(thread=self.thread, role="agent").alast()
        self.assertIsNotNone(agent_msg)
        self.assertEqual(agent_msg.content, "Event scheduled successfully!")
        
    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_tool_approval_with_rejection(self, mock_create_graph, mock_get_tools, mock_ensure_cp):
        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [AIMessage(content="")],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": False},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["type"], "completed")
        self.assertEqual(data["result"], "Action cancelled.")

        agent_msg = await Message.objects.filter(thread=self.thread, role="agent").alast()
        self.assertIsNotNone(agent_msg)
        self.assertEqual(agent_msg.content, "Action cancelled.")


    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_tool_approval_triggers_subsequent_interrupt(self, mock_create_graph, mock_get_tools, mock_ensure_cp):
        explanation = "I encountered an attendee email format issue. Let me reschedule for 9:00 PM."

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_interrupt = MagicMock()
        mock_interrupt.value = {
            "domain": "calendar",
            "tool_name": "manage_event",
            "args": {"summary": "2nd Interview"},
        }
        mock_state = MagicMock()
        mock_state.interrupts = [mock_interrupt]
        mock_state.values = {
            "messages": [AIMessage(content=explanation)],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["type"], "approval_required")
        self.assertEqual(data["result"], explanation)
        self.assertEqual(data["approval"]["domain"], "calendar")
        self.assertEqual(data["approval"]["tool_name"], "manage_event")

        agent_msg = await Message.objects.filter(thread=self.thread, role="agent").alast()
        self.assertIsNotNone(agent_msg)
        self.assertEqual(agent_msg.content, explanation)
        self.assertEqual(mock_create_graph.call_count, 1)
        mock_get_tools.assert_awaited_once_with(self.user)
        mock_ensure_cp.assert_awaited_once()
        
        
class ApprovalCardUnitTests(SimpleTestCase):
    def test_render_cards_single_approved_extracts_tool_args(self):
        approval = MagicMock(domain="calendar")
        messages = [
            AIMessage(
                content="Scheduling your interview",
                tool_calls=[
                    {
                        "name": "manage_event",
                        "args": {"summary": "Technical Interview", "start_time": "2026-09-17T10:00:00Z"},
                        "id": "call_unit_1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
        card = render_cards(approval=approval, messages=messages, approved=True)
        self.assertEqual(card["domain"], "calendar")
        self.assertTrue(card["approved"])
        self.assertEqual(card["status"], "completed")
        self.assertEqual(card["heading"], "Calendar action")
        self.assertEqual(card["args"], {"summary": "Technical Interview", "start_time": "2026-09-17T10:00:00Z"})

    def test_render_cards_single_rejected_cancelled_status(self):
        approval = MagicMock(domain="calendar")
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "manage_event", "args": {"summary": "Sync"}, "id": "call_unit_2", "type": "tool_call"}],
            )
        ]
        card = render_cards(approval=approval, messages=messages, approved=False, instruction=None)
        self.assertEqual(card["domain"], "calendar")
        self.assertFalse(card["approved"])
        self.assertEqual(card["status"], "cancelled")
        self.assertEqual(card["heading"], "Calendar action")
        self.assertEqual(card["args"], {"summary": "Sync"})

    def test_render_cards_single_revised_instruction_status(self):
        approval = MagicMock(domain="calendar")
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "manage_event", "args": {"summary": "Sync"}, "id": "call_unit_3", "type": "tool_call"}],
            )
        ]
        card = render_cards(approval=approval, messages=messages, approved=False, instruction="Reschedule for 4pm")
        self.assertEqual(card["domain"], "calendar")
        self.assertFalse(card["approved"])
        self.assertEqual(card["status"], "revised")
        self.assertEqual(card["heading"], "Calendar action")
        self.assertEqual(card["args"], {"summary": "Sync"})

    def test_render_cards_modified_args_override(self):
        approval = MagicMock(domain="email")
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "send_email", "args": {"recipient": "old@example.com", "body": "old"}, "id": "call_unit_4", "type": "tool_call"}],
            )
        ]
        modified = {"recipient": "new@example.com", "body": "updated body"}
        card = render_cards(approval=approval, messages=messages, modified_args=modified, approved=True)
        self.assertEqual(card["domain"], "email")
        self.assertEqual(card["args"], modified)

    def test_render_cards_fallback_domain_from_tool_message(self):
        approval = MagicMock(domain="")
        messages = [
            ToolMessage(name="manage_event", content="Event created successfully", tool_call_id="call_123")
        ]
        card = render_cards(approval=approval, messages=messages, approved=True)
        self.assertEqual(card["domain"], "calendar")
        self.assertEqual(card["heading"], "Calendar action")

    def test_render_cards_fallback_unknown_domain_defaults_to_general(self):
        approval = MagicMock(domain="")
        messages = []
        card = render_cards(approval=approval, messages=messages, approved=True)
        self.assertEqual(card["domain"], "general")
        self.assertEqual(card["heading"], "General action")


class ApprovalCardPersistenceTests(TransactionTestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="card_user",
            email="cards@example.com",
            password="StrongPassword123!",
        )
        self.async_client = AsyncClient()
        token = RefreshToken.for_user(self.user)
        self.auth_headers = {"Authorization": f"Bearer {token.access_token}"}
        self.thread = Thread.objects.create(
            user=self.user, name="Multi Agent Thread"
        )
        self.user_msg = Message.objects.create(
            thread=self.thread,
            role="user",
            content="Schedule meeting and send email",
        )

    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_single_approval_persists_card_record_in_db_and_response(
        self, mock_create_graph, mock_get_tools, mock_ensure_cp
    ):
        """Single approval correctly attaches card_record to Message in DB and is retrievable via messages endpoint."""
        tool_args = {
            "summary": "Weekly 1:1",
            "start_time": "2026-09-17T11:00:00Z",
        }

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [
                AIMessage(
                    content="Calendar event booked successfully!",
                    tool_calls=[
                        {
                            "name": "manage_event",
                            "args": tool_args,
                            "id": "call_p_1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["type"], "completed")
        self.assertIn("card_record", data)
        self.assertEqual(data["card_record"]["domain"], "calendar")
        self.assertEqual(data["card_record"]["status"], "completed")
        self.assertTrue(data["card_record"]["approved"])
        self.assertEqual(data["card_record"]["args"], tool_args)

        # Async DB query
        db_msg = await Message.objects.filter(
            thread=self.thread, role="agent"
        ).alast()
        self.assertIsNotNone(db_msg)
        self.assertIsInstance(db_msg.cards, list)
        self.assertEqual(len(db_msg.cards), 1)
        self.assertEqual(db_msg.cards[0], data["card_record"])

        # Async GET /api/thread/<id>/messages/
        msg_res = await self.async_client.get(
            f"/api/thread/{self.thread.id}/messages/",
            headers=self.auth_headers,
        )
        self.assertEqual(msg_res.status_code, 200)
        msg_list = msg_res.json()
        agent_msgs = [m for m in msg_list if m["role"] == "agent"]
        self.assertEqual(len(agent_msgs), 1)
        self.assertEqual(agent_msgs[0]["cards"], [data["card_record"]])


    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_single_rejection_persists_cancelled_card_in_db(
        self, mock_create_graph, mock_get_tools, mock_ensure_cp
    ):
        """Action cancellation records status='cancelled' in card and persists to Message.cards."""

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [
                AIMessage(
                    content="Action cancelled.",
                    tool_calls=[
                        {
                            "name": "manage_event",
                            "args": {"summary": "Cancelled meeting"},
                            "id": "call_p_2",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": False},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["type"], "completed")
        self.assertEqual(data["card_record"]["status"], "cancelled")
        self.assertFalse(data["card_record"]["approved"])

        db_msg = await Message.objects.filter(
            thread=self.thread, role="agent"
        ).alast()
        self.assertEqual(db_msg.cards[0]["status"], "cancelled")
        self.assertFalse(db_msg.cards[0]["approved"])

    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_single_revision_instruction_persists_revised_card_in_db(
        self, mock_create_graph, mock_get_tools, mock_ensure_cp
    ):
        """Submitting revision instruction persists status='revised' in card."""

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [
                AIMessage(
                    content="I updated the time to 4:00 PM as requested.",
                    tool_calls=[
                        {
                            "name": "manage_event",
                            "args": {"summary": "1:1 meeting"},
                            "id": "call_p_3",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": False, "instruction": "Schedule at 4pm instead"},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["card_record"]["status"], "revised")
        self.assertFalse(data["card_record"]["approved"])

        db_msg = await Message.objects.filter(
            thread=self.thread, role="agent"
        ).alast()
        self.assertEqual(db_msg.cards[0]["status"], "revised")

    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_single_approval_with_modified_args_persists_modified_args_in_card(
        self, mock_create_graph, mock_get_tools, mock_ensure_cp
    ):
        """User modified field overrides are stored in card_record args."""
        modified = {
            "summary": "Modified Interview Title",
            "start_time": "2026-09-17T16:00:00Z",
        }

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        mock_app = MagicMock()
        mock_app.astream_events = fake_astream_events
        mock_state = MagicMock()
        mock_state.interrupts = []
        mock_state.values = {
            "messages": [AIMessage(content="Event created with edited fields.")],
            "call_metrics": [],
        }
        mock_app.aget_state = AsyncMock(return_value=mock_state)
        mock_create_graph.return_value = mock_app

        response = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True, "modified_args": modified},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)

        data = await parse_sse_final(response)
        self.assertEqual(data["card_record"]["args"], modified)

        db_msg = await Message.objects.filter(
            thread=self.thread, role="agent"
        ).alast()
        self.assertEqual(db_msg.cards[0]["args"], modified)

    @patch("agent.streaming.ensure_checkpointer", new_callable=AsyncMock)
    @patch("agent.streaming.get_user_tools", new_callable=AsyncMock)
    @patch("agent.streaming.create_graph")
    async def test_multi_step_sequential_approval_workflow_persists_both_cards(
        self, mock_create_graph, mock_get_tools, mock_ensure_cp
    ):
        calendar_args = {
            "summary": "Architecture Review",
            "start_time": "2026-09-17T14:00:00Z",
        }
        email_args = {
            "recipient": "lead@example.com",
            "subject": "Meeting Invite",
            "body": "Please find link.",
        }

        async def fake_astream_events(*args, **kwargs):
            if False:
                yield {}

        # --- STEP 1: Resuming Calendar hits Email interrupt ---
        mock_app_step1 = MagicMock()
        mock_app_step1.astream_events = fake_astream_events
        mock_email_interrupt = MagicMock()
        mock_email_interrupt.value = {
            "domain": "email",
            "tool_name": "send_gmail_message",
            "args": email_args,
        }
        mock_state_step1 = MagicMock()
        mock_state_step1.interrupts = [mock_email_interrupt]
        mock_state_step1.values = {
            "messages": [
                AIMessage(
                    content="Calendar event booked. Now preparing to send the email invite.",
                    tool_calls=[
                        {
                            "name": "manage_event",
                            "args": calendar_args,
                            "id": "call_step1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "call_metrics": [],
        }
        mock_app_step1.aget_state = AsyncMock(return_value=mock_state_step1)
        mock_create_graph.return_value = mock_app_step1

        res_step1 = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(res_step1.status_code, 200)

        data_step1 = await parse_sse_final(res_step1)
        self.assertEqual(data_step1["type"], "approval_required")
        self.assertEqual(data_step1["approval"]["domain"], "email")
        self.assertEqual(data_step1["card_record"]["domain"], "calendar")
        self.assertEqual(data_step1["card_record"]["status"], "completed")
        self.assertEqual(data_step1["card_record"]["args"], calendar_args)

        # Async query of Message queryset
        agent_msgs_after_step1 = [
            msg
            async for msg in Message.objects.filter(
                thread=self.thread, role="agent"
            ).order_by("created_at", "id")
        ]
        self.assertEqual(len(agent_msgs_after_step1), 1)
        self.assertEqual(
            agent_msgs_after_step1[0].cards[0]["domain"], "calendar"
        )
        self.assertEqual(
            agent_msgs_after_step1[0].cards[0]["status"], "completed"
        )

        # --- STEP 2: Resuming Email completes the workflow ---
        mock_app_step2 = MagicMock()
        mock_app_step2.astream_events = fake_astream_events
        mock_state_step2 = MagicMock()
        mock_state_step2.interrupts = []
        mock_state_step2.values = {
            "messages": [
                AIMessage(
                    content="Email sent successfully! All steps complete.",
                    tool_calls=[
                        {
                            "name": "send_gmail_message",
                            "args": email_args,
                            "id": "call_step2",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "call_metrics": [],
        }
        mock_app_step2.aget_state = AsyncMock(return_value=mock_state_step2)
        mock_create_graph.return_value = mock_app_step2

        res_step2 = await self.async_client.post(
            f"/api/thread/{self.thread.id}/tool-approval/",
            {"approved": True},
            content_type="application/json",
            headers=self.auth_headers,
        )
        self.assertEqual(res_step2.status_code, 200)

        data_step2 = await parse_sse_final(res_step2)
        self.assertEqual(data_step2["type"], "completed")
        self.assertEqual(data_step2["card_record"]["domain"], "email")
        self.assertEqual(data_step2["card_record"]["status"], "completed")
        self.assertEqual(data_step2["card_record"]["args"], email_args)

        agent_msgs_after_step2 = [
            msg
            async for msg in Message.objects.filter(
                thread=self.thread, role="agent"
            ).order_by("created_at", "id")
        ]
        self.assertEqual(len(agent_msgs_after_step2), 2)
        self.assertEqual(
            agent_msgs_after_step2[0].cards[0]["domain"], "calendar"
        )
        self.assertEqual(
            agent_msgs_after_step2[0].cards[0]["status"], "completed"
        )
        self.assertEqual(agent_msgs_after_step2[1].cards[0]["domain"], "email")
        self.assertEqual(
            agent_msgs_after_step2[1].cards[0]["status"], "completed"
        )

        # GET messages history
        list_res = await self.async_client.get(
            f"/api/thread/{self.thread.id}/messages/",
            headers=self.auth_headers,
        )
        self.assertEqual(list_res.status_code, 200)
        messages_data = list_res.json()
        agent_history = [m for m in messages_data if m["role"] == "agent"]
        self.assertEqual(len(agent_history), 2)
        self.assertEqual(agent_history[0]["cards"][0]["domain"], "calendar")
        self.assertEqual(agent_history[0]["cards"][0]["status"], "completed")
        self.assertEqual(agent_history[0]["cards"][0]["args"], calendar_args)
        self.assertEqual(agent_history[1]["cards"][0]["domain"], "email")
        self.assertEqual(agent_history[1]["cards"][0]["status"], "completed")
        self.assertEqual(agent_history[1]["cards"][0]["args"], email_args)