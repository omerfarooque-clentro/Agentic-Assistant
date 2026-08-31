from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.models import MCPIntegration, SlackResource
from agent.tools.slack_resolver import (
    create_slack_resolver_tool,
    get_slack_id,
    is_slack_id,
    normalize_resource_name,
    normalize_resource_type,
    resolve_slack_resource,
    save_slack_id,
    search_slack,
)

User = get_user_model()


class SlackResolverNormalizationTests(TestCase):
    def test_normalize_resource_name(self):
        self.assertEqual(normalize_resource_name("#dev-learning"), "dev-learning")
        self.assertEqual(normalize_resource_name("  #general  "), "general")
        self.assertEqual(normalize_resource_name("@alice"), "alice")
        self.assertEqual(normalize_resource_name("  @Bob_Smith  "), "bob_smith")
        self.assertEqual(normalize_resource_name("random"), "random")
        self.assertEqual(normalize_resource_name(""), "")
        self.assertEqual(normalize_resource_name(None), "")

    def test_normalize_resource_type(self):
        self.assertEqual(normalize_resource_type("channel"), "channel")
        self.assertEqual(normalize_resource_type("CHANNELS"), "channel")
        self.assertEqual(normalize_resource_type("c"), "channel")
        self.assertEqual(normalize_resource_type("user"), "user")
        self.assertEqual(normalize_resource_type("users"), "user")
        self.assertEqual(normalize_resource_type("member"), "user")
        self.assertEqual(normalize_resource_type("dm"), "user")
        self.assertEqual(normalize_resource_type("team"), "team")
        self.assertEqual(normalize_resource_type("workspace"), "team")
        self.assertEqual(normalize_resource_type(""), "channel")
        self.assertEqual(normalize_resource_type(None), "channel")

    def test_is_slack_id(self):
        self.assertTrue(is_slack_id("C0123456789"))
        self.assertTrue(is_slack_id("U12345678"))
        self.assertTrue(is_slack_id("G0123456789"))
        self.assertTrue(is_slack_id("W12345678"))
        self.assertTrue(is_slack_id("B12345678"))
        self.assertFalse(is_slack_id("dev-learning"))
        self.assertFalse(is_slack_id("#dev-learning"))
        self.assertFalse(is_slack_id("@alice"))
        self.assertFalse(is_slack_id(""))
        self.assertFalse(is_slack_id(None))


class SlackResolverCacheTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="user1", email="user1@example.com", password="password")
        self.user2 = User.objects.create_user(username="user2", email="user2@example.com", password="password")

    def test_save_and_get_slack_id(self):
        # Save channel for user1
        res = save_slack_id(self.user1, "#dev-learning", "channel", "C0123456789")
        self.assertIsInstance(res, SlackResource)
        self.assertEqual(res.name, "dev-learning")
        self.assertEqual(res.resource_type, "channel")
        self.assertEqual(res.slack_id, "C0123456789")

        # Cache hit with various name formats
        self.assertEqual(get_slack_id(self.user1, "dev-learning", "channel"), "C0123456789")
        self.assertEqual(get_slack_id(self.user1, "#dev-learning", "channel"), "C0123456789")
        self.assertEqual(get_slack_id(self.user1, "#DEV-LEARNING", "channels"), "C0123456789")

        # Cache miss for another resource
        self.assertIsNone(get_slack_id(self.user1, "random-channel", "channel"))

        # User isolation: user2 should not see user1's cache
        self.assertIsNone(get_slack_id(self.user2, "dev-learning", "channel"))

    def test_save_overwrites_existing_id(self):
        save_slack_id(self.user1, "dev-learning", "channel", "C0111111111")
        self.assertEqual(get_slack_id(self.user1, "dev-learning", "channel"), "C0111111111")

        # Update ID
        save_slack_id(self.user1, "dev-learning", "channel", "C0222222222")
        self.assertEqual(get_slack_id(self.user1, "dev-learning", "channel"), "C0222222222")
        self.assertEqual(
            SlackResource.objects.filter(user=self.user1, resource_type="channel", name="dev-learning").count(),
            1,
        )


class SlackResolverSearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.integration = MCPIntegration.objects.create(
            user=self.user,
            service="slack",
            access_token="xoxp-test-token",
            enabled=True,
        )

    @patch("agent.tools.slack_resolver.requests.get")
    def test_search_slack_channel_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "ok": True,
            "channels": [
                {"id": "C0001", "name": "general", "name_normalized": "general"},
                {"id": "C0002", "name": "dev-learning", "name_normalized": "dev-learning"},
            ],
            "response_metadata": {"next_cursor": ""},
        }
        mock_get.return_value = mock_response

        resolved = search_slack(self.user, "#dev-learning", "channel")
        self.assertEqual(resolved, "C0002")

    @patch("agent.tools.slack_resolver.requests.get")
    def test_search_slack_user_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "ok": True,
            "members": [
                {"id": "U0001", "name": "alice", "real_name": "Alice Wonderland", "profile": {"display_name": "Alice"}},
                {"id": "U0002", "name": "bob", "real_name": "Bob Builder", "profile": {"display_name": "Bob"}},
            ],
            "response_metadata": {"next_cursor": ""},
        }
        mock_get.return_value = mock_response

        resolved = search_slack(self.user, "@bob", "user")
        self.assertEqual(resolved, "U0002")

    @patch("agent.tools.slack_resolver.requests.get")
    def test_search_slack_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "ok": True,
            "channels": [{"id": "C0001", "name": "general"}],
            "response_metadata": {"next_cursor": ""},
        }
        mock_get.return_value = mock_response

        resolved = search_slack(self.user, "unknown-channel", "channel")
        self.assertIsNone(resolved)


class SlackResolverWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.integration = MCPIntegration.objects.create(
            user=self.user,
            service="slack",
            access_token="xoxp-test-token",
            enabled=True,
        )

    def test_direct_slack_id_returns_as_is(self):
        # Already a Slack ID -> no DB query, returns directly
        resolved = resolve_slack_resource(self.user, "C0123456789", "channel")
        self.assertEqual(resolved, "C0123456789")

    def test_cache_hit_bypasses_slack_api(self):
        # Pre-populate DB cache
        save_slack_id(self.user, "dev-learning", "channel", "C0987654321")

        with patch("agent.tools.slack_resolver.requests.get") as mock_get:
            resolved = resolve_slack_resource(self.user, "#dev-learning", "channel")
            self.assertEqual(resolved, "C0987654321")
            mock_get.assert_not_called()

    @patch("agent.tools.slack_resolver.requests.get")
    def test_cache_miss_queries_slack_and_persists_to_cache(self, mock_get):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "ok": True,
            "channels": [{"id": "C0555555555", "name": "dev-learning"}],
            "response_metadata": {"next_cursor": ""},
        }
        mock_get.return_value = mock_response

        # 1. First call: Cache miss -> Slack API called -> Saved to DB
        resolved = resolve_slack_resource(self.user, "#dev-learning", "channel")
        self.assertEqual(resolved, "C0555555555")
        mock_get.assert_called_once()

        # Verify it was persisted to DB
        self.assertTrue(
            SlackResource.objects.filter(user=self.user, resource_type="channel", name="dev-learning").exists()
        )

        # 2. Second call: Cache hit -> Slack API NOT called again
        mock_get.reset_mock()
        second_resolved = resolve_slack_resource(self.user, "dev-learning", "channel")
        self.assertEqual(second_resolved, "C0555555555")
        mock_get.assert_not_called()

    def test_langchain_tool_execution(self):
        # Pre-populate DB cache
        save_slack_id(self.user, "dev-learning", "channel", "C0123456789")

        tool = create_slack_resolver_tool(self.user)
        self.assertEqual(tool.name, "resolve_slack_id")

        # Invoke tool with cached channel
        result = tool.invoke({"name": "#dev-learning", "resource_type": "channel"})
        self.assertEqual(result, "C0123456789")

        # Invoke tool with non-existent channel
        with patch("agent.tools.slack_resolver.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.json.return_value = {"ok": True, "channels": []}
            mock_get.return_value = mock_response

            failed_result = tool.invoke({"name": "non-existent", "resource_type": "channel"})
            self.assertIn("Could not find Slack channel 'non-existent'", failed_result)
