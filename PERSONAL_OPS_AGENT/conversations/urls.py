from django.urls import include, path
from conversations.views import ThreadListView

urlpatterns = [
    path('list_thread/', ThreadListView.as_view())
]
