"""Celery application configuration for background B2B lead generation and agent tasks."""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    from celery import Celery

    app = Celery("personal_ops_agent")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()
except ImportError:  # pragma: no cover - graceful fallback when celery is not installed
    app = None
