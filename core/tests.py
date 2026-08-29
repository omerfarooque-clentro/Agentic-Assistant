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
)
from core.serializers import (
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


