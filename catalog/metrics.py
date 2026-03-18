"""Lightweight Prometheus-compatible metrics endpoint."""

from django.http import HttpResponse
from catalog.models import Track, Artist, Genre, RecommendationFeedback, AnalyticsEvent, UserSurvey


def prometheus_metrics(request):
    """Expose key application metrics in Prometheus text format."""
    lines = []

    # Library stats
    lines.append(f'nexttrack_tracks_total {Track.objects.count()}')
    lines.append(f'nexttrack_artists_total {Artist.objects.count()}')
    lines.append(f'nexttrack_genres_total {Genre.objects.count()}')
    lines.append(f'nexttrack_tracks_analyzed {Track.objects.filter(is_audio_analyzed=True).count()}')

    # Feedback stats
    lines.append(f'nexttrack_feedback_likes {RecommendationFeedback.objects.filter(score=True).count()}')
    lines.append(f'nexttrack_feedback_dislikes {RecommendationFeedback.objects.filter(score=False).count()}')

    # Survey stats
    lines.append(f'nexttrack_surveys_total {UserSurvey.objects.count()}')

    # Event stats
    for event_type in ['search', 'recommend', 'play', 'like', 'dislike']:
        count = AnalyticsEvent.objects.filter(event_type=event_type).count()
        lines.append(f'nexttrack_events_total{{type="{event_type}"}} {count}')

    body = '\n'.join(lines) + '\n'
    return HttpResponse(body, content_type='text/plain; version=0.0.4')
