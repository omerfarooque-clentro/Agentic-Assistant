from django.urls import include, path
from conversations.views import ThreadListView, MessageListView, ThreadRenameView

urlpatterns = [
    path('list_thread/', ThreadListView.as_view()),
    path('list_messages/', MessageListView.as_view()),
    path('thread/<int:thread_id>/messages/', MessageListView.as_view()),
    path('thread/<int:thread_id>/rename/', ThreadRenameView.as_view(), name='thread-rename'),
]
