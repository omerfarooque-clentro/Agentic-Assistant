from django.test import SimpleTestCase, TestCase
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.views import LoginView, RegistrationView
from core.serializers import LoginSerializer, RegisterationSerializer
from accounts.models import User


class AuthURLTests(SimpleTestCase):
    def test_registration_url_resolves(self):
        match = resolve("/registration/")
        self.assertEqual(match.func.view_class, RegistrationView)

    def test_login_url_resolves(self):
        match = resolve("/login/")
        self.assertEqual(match.func.view_class, LoginView)


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
        # assert auth behavior, but do not test agent workflow here
        self.assertEqual(response.status_code, 200)
