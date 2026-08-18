from django.urls import path

from core import views

urlpatterns = [
    path("thread/<int:thread_id>/chat/", views.agent_chat_view, name="chat-agent"),
    path("thread/<int:thread_id>/approve-email/", views.approve_email_view, name="approve-email"),
    path("chat/", views.new_chat_view, name="new-chat"),
]
