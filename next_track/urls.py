"""URL configuration for the NextTrack project."""

import time
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.db import connection
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from catalog.metrics import prometheus_metrics

from catalog.views_web import (
    HomeView,
    PlaylistBuilderView,
    RecommendationsView,
    FeedbackListView,
    AnalyticsDashboardView,
    add_to_playlist_ajax,
    SpotifyExportInitView,
    SpotifyCallbackView,
    spotify_export_ajax,
    submit_feedback,
    create_snapshot,
    SharedPlaylistView,
    copy_shared_playlist,
    TrackCompareView,
    MoodJourneyView,
    ScatterPlotView,
    GenreLineageView,
)


def health_live(request):
    """Liveness probe — is the process running?"""
    return JsonResponse({'status': 'alive'})


def health_ready(request):
    """Readiness probe — are all dependencies available?"""
    start = time.monotonic()
    health = {'status': 'healthy', 'services': {}}

    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        health['services']['database'] = 'up'
    except Exception:
        health['services']['database'] = 'down'
        health['status'] = 'unhealthy'

    # Cache check
    try:
        from django.core.cache import cache
        cache.set('health_check', 'ok', 10)
        if cache.get('health_check') == 'ok':
            health['services']['cache'] = 'up'
        else:
            health['services']['cache'] = 'down'
            health['status'] = 'unhealthy'
    except Exception:
        health['services']['cache'] = 'down'
        health['status'] = 'unhealthy'

    # Celery worker check
    try:
        from next_track.celery import app
        result = app.control.ping(timeout=2)
        health['services']['celery'] = 'up' if result else 'down'
    except Exception:
        health['services']['celery'] = 'unknown'

    health['response_time_ms'] = round((time.monotonic() - start) * 1000, 1)

    status_code = 200 if health['status'] == 'healthy' else 503
    return JsonResponse(health, status=status_code)


urlpatterns = [
    # Health checks
    path('health/', health_ready, name='health_check'),
    path('health/ready/', health_ready, name='health_ready'),
    path('health/live/', health_live, name='health_live'),
    path('metrics/', prometheus_metrics, name='prometheus_metrics'),

    path('admin/', admin.site.urls),

    # API — current (unversioned) and v1
    path('api/', include('catalog.urls')),
    path('api/v1/', include('catalog.urls_v1')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # Web frontend
    path('', HomeView.as_view(), name='home'),
    path('builder/', PlaylistBuilderView.as_view(), name='builder'),
    path('results/', RecommendationsView.as_view(), name='results'),
    path('feedback/history/', FeedbackListView.as_view(), name='feedback_history'),
    path('analytics/', AnalyticsDashboardView.as_view(), name='analytics_dashboard'),
    path('ajax/add-track/', add_to_playlist_ajax, name='add_track_ajax'),

    # Shared playlists
    path('shared/<uuid:token>/', SharedPlaylistView.as_view(), name='shared_playlist'),
    path('ajax/create-snapshot/', create_snapshot, name='create_snapshot'),
    path('ajax/copy-playlist/<uuid:token>/', copy_shared_playlist, name='copy_shared_playlist'),

    # Track comparison
    path('compare/', TrackCompareView.as_view(), name='compare'),
    path('compare/<str:track_a>/<str:track_b>/', TrackCompareView.as_view(), name='compare_tracks'),

    # Mood journey
    path('journey/', MoodJourneyView.as_view(), name='mood_journey'),

    # Scatter plot explorer
    path('explore/', ScatterPlotView.as_view(), name='scatter_plot'),

    # Genre lineage map
    path('genre-lineage/', GenreLineageView.as_view(), name='genre_lineage'),

    # Feedback
    path('ajax/feedback/', submit_feedback, name='submit_feedback_web'),

    # Spotify export
    path('spotify/export/', SpotifyExportInitView.as_view(), name='spotify_export'),
    path('spotify/callback/', SpotifyCallbackView.as_view(), name='spotify_callback'),
    path('ajax/spotify-export/', spotify_export_ajax, name='spotify_export_ajax'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
