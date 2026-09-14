"""Broader test coverage across the repository:
JWT token refresh, in-app password changes, recovery OTP rotation,
OAuth integration management, and chat streaming endpoint validation.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from accounts.utils import generate_recovery_otp, hash_recovery_otp, verify_recovery_otp
from agent.models import MCPIntegration
from conversations.models import Thread, Message


class TokenRefreshAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="StrongPassword123!",
        )
        self.client = APIClient()

    def test_token_refresh_success_with_valid_token(self):
        refresh = RefreshToken.for_user(self.user)
        response = self.client.post("/api/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("access", data)
        self.assertTrue(len(data["access"]) > 20)

    def test_token_refresh_fails_with_invalid_token(self):
        response = self.client.post("/api/token/refresh/", {"refresh": "invalid.jwt.token"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_token_refresh_fails_with_empty_payload(self):
        response = self.client.post("/api/token/refresh/", {}, format="json")
        self.assertEqual(response.status_code, 400)


class InAppPasswordChangeTests(TestCase):
    def setUp(self):
        self.initial_otp = generate_recovery_otp()
        self.user = User.objects.create_user(
            username="changeuser",
            email="changeuser@example.com",
            password="OldPassword123!",
            otp_secret=hash_recovery_otp(self.initial_otp),
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_change_password_using_current_password(self):
        response = self.client.post(
            "/change-password/",
            {
                "current_password": "OldPassword123!",
                "new_password": "NewSecurePassword456!",
                "confirm_password": "NewSecurePassword456!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data.get("rotated_otp"))

        # Verify new password works
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewSecurePassword456!"))

    def test_change_password_using_recovery_otp(self):
        response = self.client.post(
            "/change-password/",
            {
                "otp": self.initial_otp,
                "new_password": "BrandNewPassword789!",
                "confirm_password": "BrandNewPassword789!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("rotated_otp"))
        self.assertIn("recovery_code", data)

        # Verify new password is set and old recovery OTP was rotated
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewPassword789!"))
        self.assertFalse(verify_recovery_otp(self.initial_otp, self.user.otp_secret))
        self.assertTrue(verify_recovery_otp(data["recovery_code"], self.user.otp_secret))

    def test_change_password_fails_with_wrong_credential(self):
        response = self.client.post(
            "/change-password/",
            {
                "current_password": "TotallyWrongPassword",
                "new_password": "BrandNewPassword789!",
                "confirm_password": "BrandNewPassword789!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_change_password_unauthenticated_rejected(self):
        unauth_client = APIClient()
        response = unauth_client.post(
            "/change-password/",
            {
                "current_password": "OldPassword123!",
                "new_password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 401)


class OTPGenerateAndRotationTests(TestCase):
    def setUp(self):
        self.initial_otp = generate_recovery_otp()
        self.user = User.objects.create_user(
            username="otpuser",
            email="otpuser@example.com",
            password="StrongPassword123!",
            otp_secret=hash_recovery_otp(self.initial_otp),
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_manual_otp_generation_rotates_secret(self):
        response = self.client.post(
            "/otp-generate/",
            {"password": "StrongPassword123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("recovery_code", data)

        new_otp = data["recovery_code"]
        self.user.refresh_from_db()
        # Old code invalidated, new code valid
        self.assertFalse(verify_recovery_otp(self.initial_otp, self.user.otp_secret))
        self.assertTrue(verify_recovery_otp(new_otp, self.user.otp_secret))

    def test_otp_generate_unauthenticated_rejected(self):
        unauth_client = APIClient()
        response = unauth_client.post("/otp-generate/", {"password": "pass"}, format="json")
        self.assertEqual(response.status_code, 401)


class IntegrationManagementAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="intuser",
            email="intuser@example.com",
            password="StrongPassword123!",
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    @patch("agent.integrations.views._integration_is_live", return_value=True)
    def test_integration_status_authenticated(self, mock_is_live):
        # Create an enabled calendar integration
        MCPIntegration.objects.create(
            user=self.user,
            service="calendar",
            access_token="fake-cal-token",
            enabled=True,
        )

        response = self.client.get("/api/integrations/status/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        integrations = {i["service"]: i["enabled"] for i in data.get("integrations", [])}
        self.assertTrue(integrations.get("calendar"))

    def test_integration_status_unauthenticated_rejected(self):
        unauth_client = APIClient()
        response = self.client.get("/api/integrations/status/") if False else unauth_client.get("/api/integrations/status/")
        self.assertEqual(response.status_code, 401)

    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com"})
    def test_integration_connect_returns_auth_url(self):
        response = self.client.get("/api/integrations/calendar/connect/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("authorization_url", data)
        self.assertIn("accounts.google.com", data["authorization_url"])
        self.assertIn("test-client-id", data["authorization_url"])

    def test_integration_disconnect_existing(self):
        MCPIntegration.objects.create(
            user=self.user,
            service="sheets",
            access_token="fake-sheet-token",
            enabled=True,
        )
        response = self.client.post("/api/integrations/sheets/disconnect/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("disconnected"))
        self.assertFalse(MCPIntegration.objects.filter(user=self.user, service="sheets").exists())

    def test_integration_disconnect_nonexistent_returns_404(self):
        response = self.client.post("/api/integrations/docs/disconnect/")
        self.assertEqual(response.status_code, 404)


class ChatStreamingAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chatuser",
            email="chatuser@example.com",
            password="StrongPassword123!",
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        self.thread = Thread.objects.create(user=self.user, name="Chat Thread")

    def test_chat_unauthenticated_rejected(self):
        unauth_client = APIClient()
        response = unauth_client.post(
            f"/api/thread/{self.thread.id}/chat/",
            {"message": "Hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_chat_invalid_payload_rejected(self):
        response = self.client.post(
            f"/api/thread/{self.thread.id}/chat/",
            {"wrong_field": "Hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_chat_nonexistent_thread_returns_404(self):
        response = self.client.post(
            "/api/thread/999999/chat/",
            {"message": "Hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    @patch("core.views.run_agent")
    def test_chat_streaming_headers_and_thread_message_creation(self, mock_run_agent):
        async def fake_run_agent(*args, **kwargs):
            yield {"type": "status", "status": "thinking", "message": "Planning..."}
            yield {"type": "token", "token": "Hello "}
            yield {"type": "token", "token": "there!"}
            yield {
                "type": "completed",
                "result": {"messages": [MagicMock(content="Hello there!")]},
                "metrics": {"total_tokens": 10},
            }

        mock_run_agent.side_effect = fake_run_agent

        response = self.client.post(
            f"/api/thread/{self.thread.id}/chat/",
            {"message": "Hi, assistant!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["Cache-Control"], "no-cache")

        # Verify user message was persisted in the DB
        user_msg = Message.objects.filter(thread=self.thread, role="user").last()
        self.assertIsNotNone(user_msg)
        self.assertEqual(user_msg.content, "Hi, assistant!")
