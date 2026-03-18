"""
WSGI config for NextTrack project.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'next_track.settings.production')
application = get_wsgi_application()
