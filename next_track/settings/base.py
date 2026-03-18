"""Django base settings for the NextTrack project."""

import os
from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# =============================================================================
# SECURITY
# =============================================================================
from django.core.management.utils import get_random_secret_key
_default_secret_key = get_random_secret_key()
SECRET_KEY = config('SECRET_KEY', default=_default_secret_key)

DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1,testserver', cast=lambda v: [s.strip() for s in v.split(',')])

# =============================================================================
# INSTALLED APPS
# =============================================================================
INSTALLED_APPS = [
    'daphne',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'corsheaders',
    'django_filters',
    'channels',
    'drf_spectacular',

    # Project app
    'catalog.apps.CatalogConfig',
]

# =============================================================================
# MIDDLEWARE
# =============================================================================
MIDDLEWARE = [
    'catalog.middleware.RequestIDMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'catalog.middleware.ContentSecurityPolicyMiddleware',
]

ROOT_URLCONF = 'next_track.urls'

# =============================================================================
# TEMPLATES
# =============================================================================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'catalog' / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'catalog.context_processors.playlist_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'next_track.wsgi.application'
ASGI_APPLICATION = 'next_track.asgi.application'

# =============================================================================
# DATABASE
# =============================================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB', default='music_db'),
        'USER': config('POSTGRES_USER', default='music_user'),
        'PASSWORD': config('POSTGRES_PASSWORD', default='music_pass'),
        'HOST': config('POSTGRES_HOST', default='db'),
        'PORT': config('POSTGRES_PORT', default='5432'),
        'OPTIONS': {
            'connect_timeout': 5,
        },
    }
}

# =============================================================================
# PASSWORD VALIDATION
# =============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =============================================================================
# INTERNATIONALIZATION
# =============================================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# =============================================================================
# STATIC FILES
# =============================================================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# DJANGO REST FRAMEWORK
# =============================================================================
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],

    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,

    'DEFAULT_FILTER_BACKENDS': ['django_filters.rest_framework.DjangoFilterBackend'],

    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',

    'DEFAULT_VERSIONING_CLASS': 'rest_framework.versioning.URLPathVersioning',

    'EXCEPTION_HANDLER': 'catalog.exceptions.custom_exception_handler',

    # Rate limiting
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/day',
        'anon_burst': '20/minute',
        'recommend': '50/hour',
        'recommend_burst': '10/minute',
        'statistics': '30/hour',
        'search': '200/hour',
        'export': '5/hour',
        'feedback': '100/hour',
    },
}

# =============================================================================
# DRF-SPECTACULAR (OpenAPI SCHEMA)
# =============================================================================
SPECTACULAR_SETTINGS = {
    'TITLE': 'NextTrack Music Recommendation API',
    'DESCRIPTION': (
        'Content-based music recommendation engine using 5D audio feature vectors '
        '(valence, energy, danceability, acousticness, tempo). Supports playlist-based '
        'recommendations, hybrid search (local DB + Spotify), library statistics, '
        'user surveys, and analytics event tracking.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# =============================================================================
# CELERY
# =============================================================================
CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

from celery.schedules import crontab  # noqa: E402
CELERY_BEAT_SCHEDULE = {
    'warm-popular-cache-daily': {
        'task': 'catalog.tasks.warm_cache_for_popular_tracks',
        'schedule': crontab(hour=3, minute=0),
        'kwargs': {'popularity_threshold': 70, 'limit': 10},
    },
    'harvest-tracks-biweekly': {
        'task': 'catalog.tasks.harvest_batch_from_popular_tracks',
        'schedule': crontab(day_of_week='monday,thursday', hour=2, minute=0),
        'kwargs': {'popularity_threshold': 80, 'tracks_limit': 50, 'recs_per_track': 10},
    },
    'cleanup-stale-data-weekly': {
        'task': 'catalog.tasks.cleanup_stale_data',
        'schedule': crontab(day_of_week='sunday', hour=4, minute=0),
    },
    'refresh-artist-cache-12h': {
        'task': 'catalog.tasks.refresh_popular_artist_cache',
        'schedule': crontab(hour='*/12', minute=30),
    },
    'materialize-popular-recommendations-daily': {
        'task': 'catalog.tasks.materialize_popular_recommendations',
        'schedule': crontab(hour=4, minute=0),
        'kwargs': {'popularity_threshold': 70, 'limit': 50},
    },
}

# =============================================================================
# REDIS CACHE
# =============================================================================
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': config('REDIS_URL', default='redis://localhost:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

# =============================================================================
# DJANGO CHANNELS (WebSocket)
# =============================================================================
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [(config('REDIS_HOST', default='redis'), 6379)],
        },
    },
}

# =============================================================================
# CORS
# =============================================================================
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# =============================================================================
# SPOTIFY API
# =============================================================================
SPOTIFY_CLIENT_ID = config('SPOTIFY_CLIENT_ID', default='')
SPOTIFY_CLIENT_SECRET = config('SPOTIFY_CLIENT_SECRET', default='')

# =============================================================================
# LAST.FM API (Optional)
# =============================================================================
LASTFM_API_KEY = config('LASTFM_API_KEY', default='')

# =============================================================================
# GENIUS API (Optional)
# =============================================================================
GENIUS_ACCESS_TOKEN = config('GENIUS_ACCESS_TOKEN', default='')

# =============================================================================
# EXTERNAL API TIMEOUTS (seconds)
# =============================================================================
EXTERNAL_API_CONNECT_TIMEOUT = config('EXTERNAL_API_CONNECT_TIMEOUT', default=5, cast=int)
EXTERNAL_API_READ_TIMEOUT = config('EXTERNAL_API_READ_TIMEOUT', default=15, cast=int)

# =============================================================================
# SESSION
# =============================================================================
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_AGE = 86400 * 7  # 7 days

# =============================================================================
# CONTENT SECURITY POLICY
# =============================================================================
# Using django-csp if installed, otherwise set via middleware
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com")
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdnjs.cloudflare.com")
CSP_FONT_SRC = ("'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com")
CSP_IMG_SRC = ("'self'", "data:", "https://i.scdn.co", "https://*.spotify.com")
CSP_CONNECT_SRC = ("'self'", "wss:", "ws:")
CSP_FRAME_SRC = ("'self'", "https://open.spotify.com")

# =============================================================================
# LOGGING
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
        'verbose': {
            'format': '{levelname} {asctime} {module} [{request_id}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['request_id'],
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'catalog': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
