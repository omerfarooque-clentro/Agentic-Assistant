from django.test import SimpleTestCase, TestCase
from django.urls import resolve
from rest_framework.test import APIClient

from accounts.models import User
from conversations.models import Approval, Message, Thread
from conversations.serializer import MessageSerializer, ThreadSerializer
from conversations.views import MessageListView, ThreadListView


class ConversationURLTests(SimpleTestCase):
    def test_thread_list_url_resolves(self):
        match = resolve('/api/list_thread/')
        self.assertEqual(match.func.view_class, ThreadListView)

    def test_message_list_url_resolves(self):
        match = resolve('/api/thread/1/messages/')
        self.assertEqual(match.func.view_class, MessageListView)


class ConversationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testops", email="ops@test.com", password="Password123!")

    def test_thread_creation_and_defaults(self):
        thread = Thread.objects.create(user=self.user)
        self.assertEqual(thread.name, "New Thread")
        self.assertEqual(str(thread), f"New Thread — {self.user.username}")
        self.assertIsNotNone(thread.created_at)
        self.assertIsNotNone(thread.updated_at)

    def test_message_creation_with_metrics(self):
        thread = Thread.objects.create(user=self.user, name="Project Sync")
        metrics_data = {
            "total_tokens": 1250,
            "input_tokens": 1000,
            "output_tokens": 250,
            "latency_s": "1.45",
            "model": "openai/gpt-oss-120b",
            "breakdown": [
                {"name": "General Agent (Call #2)", "total_tokens": 1250, "latency_ms": 1450}
            ]
        }
        msg = Message.objects.create(
            thread=thread,
            role="agent",
            content="I have updated your tasks.",
            metrics=metrics_data,
        )
        self.assertEqual(msg.role, "agent")
        self.assertEqual(msg.metrics["total_tokens"], 1250)
        self.assertEqual(msg.metrics["model"], "openai/gpt-oss-120b")
        self.assertEqual(len(msg.metrics["breakdown"]), 1)

    def test_message_creation_without_metrics(self):
        thread = Thread.objects.create(user=self.user)
        msg = Message.objects.create(thread=thread, role="user", content="Hello!")
        self.assertIsNone(msg.metrics)
        self.assertEqual(str(msg), "user: Hello!...")

    def test_approval_creation_and_str(self):
        thread = Thread.objects.create(user=self.user, name="Email Dispatch")
        msg = Message.objects.create(thread=thread, role="agent", content="Draft ready for approval")
        appr = Approval.objects.create(thread=thread, message=msg, domain="email", approved=True)
        self.assertTrue(appr.approved)
        self.assertIn("Email Dispatch", str(appr))
        self.assertIn("Approved", str(appr))


class ConversationSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testser", email="ser@test.com", password="Password123!")
        self.thread = Thread.objects.create(user=self.user, name="Serialization Thread")

    def test_thread_serializer(self):
        serializer = ThreadSerializer(instance=self.thread)
        self.assertEqual(serializer.data["name"], "Serialization Thread")
        self.assertIn("id", serializer.data)
        self.assertIn("created_at", serializer.data)

    def test_message_serializer_includes_metrics(self):
        metrics_data = {"total_tokens": 500, "latency_s": "0.80"}
        msg = Message.objects.create(
            thread=self.thread,
            role="agent",
            content="Serialized agent response",
            metrics=metrics_data,
        )
        serializer = MessageSerializer(instance=msg)
        self.assertEqual(serializer.data["metrics"], metrics_data)
        self.assertEqual(serializer.data["content"], "Serialized agent response")
        self.assertEqual(serializer.data["role"], "agent")


class ConversationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username="alice", email="alice@test.com", password="Password123!")
        self.user2 = User.objects.create_user(username="bob", email="bob@test.com", password="Password123!")

        self.thread1 = Thread.objects.create(user=self.user1, name="Alice Thread 1")
        self.thread2 = Thread.objects.create(user=self.user2, name="Bob Thread 1")

    def test_unauthenticated_requests_rejected(self):
        res = self.client.get('/api/list_thread/')
        self.assertEqual(res.status_code, 401)

    def test_user_isolation_thread_list(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.get('/api/list_thread/')
        self.assertEqual(res.status_code, 200)
        thread_ids = [t["id"] for t in res.data]
        self.assertIn(self.thread1.id, thread_ids)
        self.assertNotIn(self.thread2.id, thread_ids)

    def test_create_thread_api(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.post('/api/list_thread/', {"name": "New Alice Thread"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["name"], "New Alice Thread")
        self.assertTrue(Thread.objects.filter(id=res.data["id"], user=self.user1).exists())

    def test_message_list_with_metrics(self):
        metrics_payload = {"total_tokens": 850, "latency_s": "1.12", "breakdown": []}
        Message.objects.create(thread=self.thread1, role="user", content="What is on my calendar?")
        Message.objects.create(thread=self.thread1, role="agent", content="You have no events.", metrics=metrics_payload)

        self.client.force_authenticate(user=self.user1)
        res = self.client.get(f'/api/thread/{self.thread1.id}/messages/')
        self.assertEqual(res.status_code, 200)
        messages = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(messages), 2)
        agent_msg = [m for m in messages if m["role"] == "agent"][0]
        self.assertIsNotNone(agent_msg["metrics"])
        self.assertEqual(agent_msg["metrics"]["total_tokens"], 850)

    def test_cannot_access_other_user_thread_messages(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.get(f'/api/thread/{self.thread2.id}/messages/')
        self.assertEqual(res.status_code, 404)
