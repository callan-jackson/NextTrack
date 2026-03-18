"""Development settings — DEBUG=True, relaxed security, verbose logging."""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver', '0.0.0.0']

CORS_ALLOW_ALL_ORIGINS = True

# Relaxed throttle rates for development
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {
    'anon': '1000/day',
    'anon_burst': '100/minute',
    'recommend': '500/hour',
    'recommend_burst': '50/minute',
    'statistics': '300/hour',
    'search': '2000/hour',
    'export': '50/hour',
    'feedback': '1000/hour',
}

# Use console email backend
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Query profiling with django-silk (install with: pip install django-silk)
try:
    import silk  # noqa: F401
    INSTALLED_APPS += ['silk']
    MIDDLEWARE += ['silk.middleware.SilkyMiddleware']
    SILKY_PYTHON_PROFILER = True
    SILKY_MAX_RECORDED_REQUESTS = 10000
    SILKY_META = True
except ImportError:
    pass
