"""Celery configuration for NextTrack async tasks."""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'next_track.settings')

app = Celery('next_track')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
