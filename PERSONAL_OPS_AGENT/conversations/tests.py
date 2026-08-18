from django.test import SimpleTestCase
from django.urls import resolve

from conversations.views import ThreadListView


class ConversationURLTests(SimpleTestCase):
    def test_thread_list_url_resolves(self):
        match = resolve('/api/list_thread')
        self.assertEqual(match.func.view_class, ThreadListView)
