from django.urls import path

from core import views
from agent.integrations import views as integration_views

urlpatterns = [
    path("thread/<int:thread_id>/chat/", views.agent_chat_view, name="chat-agent"),
    path("thread/<int:thread_id>/tool-approval/", views.tool_approval_view, name="approve-email"),
    path("thread/<int:thread_id>/cancel/", views.cancel_thread_execution_view, name="cancel-thread-execution"),
    path("thread/<int:thread_id>/background-lead-job/", views.start_background_lead_job_view, name="start-background-lead-job"),
    path("thread/<int:thread_id>/background-lead-job/<str:job_id>/", views.background_lead_job_status_view, name="background-lead-job-status"),
    path("leads/download/", views.download_leads_file_view, name="download-leads-default"),
    path("leads/download/<str:fmt>/", views.download_leads_file_view, name="download-leads-file"),
    path("thread/<int:thread_id>/delete/", views.delete_thread_view, name="delete-thread"),
    path("chat/", views.new_chat_view, name="new-chat"),
    path("integrations/<str:service>/connect/", integration_views.integration_connect_view, name="integration-connect"),
    path("integrations/<str:service>/callback/", integration_views.integration_callback_view, name="integration-callback"),
    path("integrations/<str:service>/disconnect/", integration_views.integration_disconnect_view, name="integration-disconnect"),
    path("integrations/status/", integration_views.integration_status_view, name="integration-status"),
]


