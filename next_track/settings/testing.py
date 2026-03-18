"""Test settings — fast hashing, eager Celery, in-memory caching."""

from .base import *  # noqa: F401,F403

DEBUG = False

# Use SQLite for fast local test runs (no PostgreSQL dependency)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Faster password hashing for tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Synchronous Celery for predictable test execution
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In-memory cache for test isolation
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

# Disable global throttling but keep scoped rates for views that set them explicitly
REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
