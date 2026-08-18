from django.urls import path

from core import views

urlpatterns = [
    path("chat/", views.agent_chat_view, name="chat-agent"),
    path("approve-email/", views.approve_email_view, name="approve-email"),
]
