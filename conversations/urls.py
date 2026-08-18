from django.urls import include, path
from conversations.views import ThreadListView, MessageListView

urlpatterns = [
    path('list_thread/', ThreadListView.as_view()),
    path('list_messages/', MessageListView.as_view()),
]
