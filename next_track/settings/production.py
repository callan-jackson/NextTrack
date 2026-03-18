"""Production settings — strict security, JSON logging, no debug."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

CORS_ALLOW_ALL_ORIGINS = False

# Production security
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Trust the X-Forwarded-Proto header from reverse proxies (AWS ALB, nginx)
# so SECURE_SSL_REDIRECT doesn't cause infinite redirect loops.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# =============================================================================
# REDIS SENTINEL (High Availability)
# =============================================================================
REDIS_SENTINEL_ENABLED = os.environ.get('REDIS_SENTINEL_ENABLED', 'False').lower() in ('true', '1', 'yes')

if REDIS_SENTINEL_ENABLED:
    SENTINEL_HOSTS = os.environ.get('REDIS_SENTINEL_HOSTS', 'sentinel:26379')
    SENTINELS = [
        (host.strip().split(':')[0], int(host.strip().split(':')[1]))
        for host in SENTINEL_HOSTS.split(',')
    ]
    SENTINEL_MASTER_NAME = os.environ.get('REDIS_SENTINEL_MASTER', 'mymaster')
    SENTINEL_PASSWORD = os.environ.get('REDIS_SENTINEL_PASSWORD', '')
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')

    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': f'redis://{SENTINEL_MASTER_NAME}/1',
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.SentinelClient',
                'SENTINELS': SENTINELS,
                'SENTINEL_KWARGS': {
                    'password': SENTINEL_PASSWORD,
                } if SENTINEL_PASSWORD else {},
                'PASSWORD': REDIS_PASSWORD,
            },
        }
    }

    # Celery broker / result backend via Sentinel
    CELERY_BROKER_URL = f'sentinel://:{REDIS_PASSWORD}@{SENTINEL_HOSTS}/0'
    CELERY_RESULT_BACKEND = f'sentinel://:{REDIS_PASSWORD}@{SENTINEL_HOSTS}/0'
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        'master_name': SENTINEL_MASTER_NAME,
        'sentinel_kwargs': {'password': SENTINEL_PASSWORD} if SENTINEL_PASSWORD else {},
    }
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = CELERY_BROKER_TRANSPORT_OPTIONS

# =============================================================================
# DATABASE READ REPLICA
# =============================================================================
DATABASE_REPLICA_HOST = os.environ.get('DATABASE_REPLICA_HOST', '')

if DATABASE_REPLICA_HOST:
    DATABASES['replica'] = {
        'ENGINE': DATABASES['default']['ENGINE'],
        'NAME': DATABASES['default']['NAME'],
        'USER': DATABASES['default']['USER'],
        'PASSWORD': DATABASES['default']['PASSWORD'],
        'HOST': DATABASE_REPLICA_HOST,
        'PORT': DATABASES['default']['PORT'],
        'OPTIONS': DATABASES['default'].get('OPTIONS', {}),
    }

    DATABASE_ROUTERS = ['catalog.db_router.ReadReplicaRouter']

# =============================================================================
# STRUCTURED JSON LOGGING
# =============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {
            '()': 'catalog.middleware.RequestIDFilter',
        },
    },
    'formatters': {
        'json': {
            '()': 'catalog.logging.JSONFormatter',
        },
        'verbose': {
            'format': '{levelname} {asctime} {module} [{request_id}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'json_stdout': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'json',
            'filters': ['request_id'],
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['request_id'],
        },
    },
    'loggers': {
        'django': {
            'handlers': ['json_stdout'],
            'level': 'INFO',
            'propagate': True,
        },
        'catalog': {
            'handlers': ['json_stdout'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
