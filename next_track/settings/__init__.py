"""Settings package.

Importing ``next_track.settings`` directly (e.g. the Celery entrypoint or any
tooling that has not picked an explicit environment module) resolves to the
development settings. Choose a specific environment with
``DJANGO_SETTINGS_MODULE`` set to one of:

* ``next_track.settings.development`` — local dev (DEBUG on, relaxed throttles)
* ``next_track.settings.testing``     — SQLite in-memory, eager Celery (used by CI)
* ``next_track.settings.production``  — strict security, JSON logging
"""

from .development import *  # noqa: F401,F403
