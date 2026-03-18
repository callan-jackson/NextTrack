"""API views for the music catalog (genres, artists, tracks, recommendations)."""

import csv
import logging
from django.core.cache import cache
from django.db import models
from django.db.models import Avg, Count
from django.http import StreamingHttpResponse
from rest_framework import viewsets, status, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter, inline_serializer

from catalog.models import Genre, Artist, Track, UserSurvey, AnalyticsEvent
from catalog.serializers import (
    GenreSerializer,
    ArtistSerializer,
    TrackSerializer,
    TrackListSerializer,
    RecommendationRequestSerializer,
)
from catalog.services import get_recommendations_from_sequence, search_tracks, euclidean_distance, get_feature_vector, get_genre_lineage_data
from catalog.pagination import CursorPaginationByPopularity

logger = logging.getLogger(__name__)


def safe_cache_get(key):
    """Read from cache, returning None on any backend error."""
    try:
        return cache.get(key)
    except Exception as e:
        logger.warning(f"Cache read failed for key '{key}': {e}")
        return None


def safe_cache_set(key, value, timeout=None):
    """Write to cache, silently logging any backend error."""
    try:
        cache.set(key, value, timeout=timeout)
    except Exception as e:
        logger.warning(f"Cache write failed for key '{key}': {e}")


class GenreViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only endpoint for genres."""
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    filterset_fields = ['name']


class ArtistViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only endpoint for artists."""
    queryset = Artist.objects.all()
    serializer_class = ArtistSerializer
    filterset_fields = ['name', 'popularity']


MOOD_QUADRANT_FILTERS = {
    'happy_energetic': {'valence__gte': 0.5, 'energy__gte': 0.5},
    'happy_calm': {'valence__gte': 0.5, 'energy__lt': 0.5},
    'sad_energetic': {'valence__lt': 0.5, 'energy__gte': 0.5},
    'sad_calm': {'valence__lt': 0.5, 'energy__lt': 0.5},
}

POPULARITY_TIER_RANGES = {
    'mainstream': (80, 101),
    'popular': (60, 80),
    'underground': (40, 60),
    'hidden_gems': (0, 40),
}


class TrackViewSet(viewsets.ReadOnlyModelViewSet):
    """Track API with recommend, statistics, search, similar, and export actions."""
    queryset = Track.objects.select_related('artist').prefetch_related('genres')
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['popularity', 'artist']

    def get_serializer_class(self):
        """Use lighter serializer for list view, full one for detail."""
        if self.action == 'list':
            return TrackListSerializer
        return TrackSerializer

    def get_pagination_class(self):
        if self.action == 'list':
            return CursorPaginationByPopularity
        return None

    @property
    def pagination_class(self):
        if self.action == 'list':
            return CursorPaginationByPopularity
        return None

    @extend_schema(
        summary="Generate track recommendations",
        description=(
            "Accepts a list of track IDs (a playlist) and returns recommended tracks "
            "ranked by Euclidean distance from the playlist's audio-feature centroid. "
            "Optional preferences bias the centroid toward desired feature values."
        ),
        request=inline_serializer(
            name='RecommendRequest',
            fields={
                'track_ids': drf_serializers.ListField(
                    child=drf_serializers.CharField(),
                    help_text="Spotify track IDs forming the seed playlist (1-50).",
                ),
                'preferences': drf_serializers.DictField(
                    child=drf_serializers.FloatField(),
                    required=False,
                    help_text="Optional feature biases: valence, energy, danceability, acousticness, tempo.",
                ),
                'limit': drf_serializers.IntegerField(
                    required=False, default=10,
                    help_text="Max recommendations to return (1-50).",
                ),
            },
        ),
        responses={200: TrackSerializer(many=True)},
        examples=[
            OpenApiExample(
                'Recommend request',
                value={
                    'track_ids': ['4uLU6hMCjMI75M1A2tKUQC', '3n3Ppam7vgaVa1iaRUc9Lp'],
                    'preferences': {'energy': 0.8, 'valence': 0.7},
                    'limit': 5,
                },
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=['post'], throttle_classes=[ScopedRateThrottle])
    def recommend(self, request: Request) -> Response:
        """Generate recommendations from a list of track IDs using centroid matching."""
        self.throttle_scope = 'recommend'

        track_ids = request.data.get('track_ids', [])
        preferences = request.data.get('preferences', {})
        limit = request.data.get('limit', 10)

        if not track_ids:
            return Response(
                {'error': 'track_ids is required and must be a non-empty list'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(track_ids, list):
            return Response(
                {'error': 'track_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(track_ids) > 50:
            return Response(
                {'error': 'Maximum 50 tracks allowed in sequence'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if preferences and not isinstance(preferences, dict):
            return Response(
                {'error': 'preferences must be a dictionary'},
                status=status.HTTP_400_BAD_REQUEST
            )

        valid_pref_keys = {'valence', 'energy', 'danceability', 'acousticness', 'tempo'}
        if preferences:
            invalid_keys = set(preferences.keys()) - valid_pref_keys
            if invalid_keys:
                return Response(
                    {'error': f'Invalid preference keys: {invalid_keys}. Valid keys: {valid_pref_keys}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        limit = min(max(1, int(limit)), 50)

        # Same inputs always give same results, so we can cache
        cache_key = f"rec_seq_{hash(tuple(sorted(track_ids)))}_{hash(frozenset(preferences.items()) if preferences else '')}_{limit}"
        cached_result = safe_cache_get(cache_key)

        if cached_result:
            return Response(cached_result)

        result = get_recommendations_from_sequence(
            track_ids=track_ids,
            preferences=preferences,
            limit=limit
        )

        if not result['input_tracks']:
            return Response(
                {'error': 'No valid tracks found for the provided track_ids'},
                status=status.HTTP_404_NOT_FOUND
            )

        response_data = {
            'input_tracks': TrackSerializer(result['input_tracks'], many=True).data,
            'centroid': result['centroid'],
            'recommendations': TrackSerializer(result['recommendations'], many=True).data,
            'count': len(result['recommendations']),
        }

        safe_cache_set(cache_key, response_data, timeout=86400)  # 24h cache

        return Response(response_data)

    @extend_schema(
        summary="Library statistics",
        description=(
            "Returns aggregate statistics for the track library: totals, "
            "average audio features, genre breakdown, popularity distribution, "
            "and top artists. Cached for 1 hour."
        ),
        responses={200: inline_serializer(
            name='LibraryStatistics',
            fields={
                'total_tracks': drf_serializers.IntegerField(),
                'total_artists': drf_serializers.IntegerField(),
                'total_genres': drf_serializers.IntegerField(),
                'averages': drf_serializers.DictField(),
                'genres_breakdown': drf_serializers.ListField(),
                'popularity_distribution': drf_serializers.DictField(),
                'top_artists': drf_serializers.ListField(),
            },
        )},
    )
    @action(detail=False, methods=['get'], throttle_classes=[ScopedRateThrottle])
    def statistics(self, request: Request) -> Response:
        """Return aggregate stats about the library (cached 1h)."""
        self.throttle_scope = 'statistics'

        cache_key = "library_statistics"
        cached_stats = safe_cache_get(cache_key)
        if cached_stats:
            return Response(cached_stats)

        total_tracks = Track.objects.count()
        total_artists = Artist.objects.count()
        total_genres = Genre.objects.count()

        averages = Track.objects.aggregate(
            avg_tempo=Avg('tempo'),
            avg_energy=Avg('energy'),
            avg_valence=Avg('valence'),
            avg_danceability=Avg('danceability'),
            avg_acousticness=Avg('acousticness'),
            avg_loudness=Avg('loudness'),
            avg_popularity=Avg('popularity'),
        )

        genres_breakdown = list(
            Genre.objects.annotate(
                track_count=Count('tracks')
            ).values('name', 'track_count').order_by('-track_count')[:20]
        )

        from django.db.models import Case, When, IntegerField
        pop_agg = Track.objects.aggregate(
            very_popular=Count(Case(When(popularity__gte=80, then=1), output_field=IntegerField())),
            popular=Count(Case(When(popularity__gte=60, popularity__lt=80, then=1), output_field=IntegerField())),
            moderate=Count(Case(When(popularity__gte=40, popularity__lt=60, then=1), output_field=IntegerField())),
            low=Count(Case(When(popularity__gte=20, popularity__lt=40, then=1), output_field=IntegerField())),
            very_low=Count(Case(When(popularity__lt=20, then=1), output_field=IntegerField())),
        )
        popularity_ranges = {
            'very_popular (80-100)': pop_agg['very_popular'],
            'popular (60-79)': pop_agg['popular'],
            'moderate (40-59)': pop_agg['moderate'],
            'low (20-39)': pop_agg['low'],
            'very_low (0-19)': pop_agg['very_low'],
        }

        top_artists = list(
            Artist.objects.annotate(
                track_count=Count('tracks')
            ).values('name', 'track_count', 'popularity').order_by('-track_count')[:10]
        )

        response_data = {
            'total_tracks': total_tracks,
            'total_artists': total_artists,
            'total_genres': total_genres,
            'averages': {
                'tempo': round(averages['avg_tempo'] or 0, 2),
                'energy': round(averages['avg_energy'] or 0, 3),
                'valence': round(averages['avg_valence'] or 0, 3),
                'danceability': round(averages['avg_danceability'] or 0, 3),
                'acousticness': round(averages['avg_acousticness'] or 0, 3),
                'loudness': round(averages['avg_loudness'] or 0, 2),
                'popularity': round(averages['avg_popularity'] or 0, 1),
            },
            'genres_breakdown': genres_breakdown,
            'popularity_distribution': popularity_ranges,
            'top_artists': top_artists,
        }

        safe_cache_set(cache_key, response_data, timeout=3600)  # 1h cache

        return Response(response_data)

    @extend_schema(
        summary="Search tracks",
        description=(
            "Hybrid search: queries the local database first, then falls back to "
            "Spotify to ingest new tracks. Results are deduplicated and ranked by "
            "relevance (artist match > title match > genre match > popularity)."
        ),
        parameters=[
            OpenApiParameter(
                name='q', type=str, location=OpenApiParameter.QUERY,
                required=True, description='Search query (min 2 characters).',
            ),
            OpenApiParameter(
                name='limit', type=int, location=OpenApiParameter.QUERY,
                required=False, description='Max results to return (default 20, max 50).',
            ),
        ],
        responses={200: inline_serializer(
            name='SearchResults',
            fields={
                'query': drf_serializers.CharField(),
                'results': TrackListSerializer(many=True),
                'count': drf_serializers.IntegerField(),
            },
        )},
    )
    @action(detail=False, methods=['get'], throttle_classes=[ScopedRateThrottle])
    def search(self, request: Request) -> Response:
        """Search tracks in local DB, falls back to Spotify if needed."""
        self.throttle_scope = 'search'

        query = request.query_params.get('q', '')
        limit = min(int(request.query_params.get('limit', 20)), 50)

        if len(query) < 2:
            return Response(
                {'error': 'Search query must be at least 2 characters'},
                status=status.HTTP_400_BAD_REQUEST
            )

        tracks = search_tracks(query, limit=limit)

        # Apply optional filters
        mood = request.query_params.get('mood')
        if mood and mood in MOOD_QUADRANT_FILTERS:
            tracks = [t for t in tracks if all(
                getattr(t, f.replace('__gte', '').replace('__lt', '')) >= v if '__gte' in f
                else getattr(t, f.replace('__lt', '')) < v
                for f, v in MOOD_QUADRANT_FILTERS[mood].items()
            )]

        min_bpm = request.query_params.get('min_bpm')
        max_bpm = request.query_params.get('max_bpm')
        if min_bpm:
            tracks = [t for t in tracks if t.tempo >= float(min_bpm)]
        if max_bpm:
            tracks = [t for t in tracks if t.tempo <= float(max_bpm)]

        popularity_tier = request.query_params.get('popularity_tier')
        if popularity_tier and popularity_tier in POPULARITY_TIER_RANGES:
            lo, hi = POPULARITY_TIER_RANGES[popularity_tier]
            tracks = [t for t in tracks if lo <= t.popularity < hi]

        return Response({
            'query': query,
            'results': TrackListSerializer(tracks, many=True).data,
            'count': len(tracks),
        })

    @extend_schema(
        summary="Find similar tracks",
        description="Returns tracks most similar to the given track based on audio feature distance.",
        parameters=[
            OpenApiParameter(name='limit', type=int, location=OpenApiParameter.QUERY,
                             required=False, description='Max results (default 10, max 50).'),
        ],
        responses={200: TrackSerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def similar(self, request: Request, pk=None) -> Response:
        """Find tracks most similar to a given track by audio features."""
        try:
            track = Track.objects.select_related('artist').get(pk=pk)
        except Track.DoesNotExist:
            return Response({'error': 'Track not found'}, status=status.HTTP_404_NOT_FOUND)

        limit = min(int(request.query_params.get('limit', 10)), 50)
        cache_key = f"similar_{pk}_{limit}"
        cached = safe_cache_get(cache_key)
        if cached:
            return Response(cached)

        import numpy as np
        target_vec = get_feature_vector(track)
        candidates = Track.objects.select_related('artist').prefetch_related('genres').exclude(pk=pk)

        scored = []
        for candidate in candidates.iterator(chunk_size=500):
            c_vec = get_feature_vector(candidate)
            dist = euclidean_distance(target_vec, c_vec)
            similarity = max(0, round((1 - dist) * 100, 2))
            scored.append((candidate, similarity))

        scored.sort(key=lambda x: -x[1])
        top = scored[:limit]

        results = []
        for t, score in top:
            data = TrackSerializer(t).data
            data['similarity_score'] = score
            results.append(data)

        safe_cache_set(cache_key, results, timeout=43200)  # 12h
        return Response(results)

    @extend_schema(
        summary="Export tracks as JSON or CSV",
        description="Export a set of tracks by IDs in JSON or CSV format.",
        parameters=[
            OpenApiParameter(name='track_ids', type=str, location=OpenApiParameter.QUERY,
                             required=True, description='Comma-separated track IDs.'),
            OpenApiParameter(name='format', type=str, location=OpenApiParameter.QUERY,
                             required=False, description='Export format: json or csv (default json).'),
        ],
    )
    @action(detail=False, methods=['get'], throttle_classes=[ScopedRateThrottle])
    def export(self, request: Request) -> Response:
        """Export tracks as JSON or CSV download."""
        self.throttle_scope = 'export'

        track_ids_str = request.query_params.get('track_ids', '')
        if not track_ids_str:
            return Response({'error': 'track_ids query parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        track_ids = [tid.strip() for tid in track_ids_str.split(',') if tid.strip()]
        tracks = Track.objects.filter(pk__in=track_ids).select_related('artist').prefetch_related('genres')

        export_format = request.query_params.get('format', 'json').lower()

        if export_format == 'csv':
            def csv_generator():
                import io
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['id', 'title', 'artist', 'popularity', 'energy', 'valence',
                                 'danceability', 'acousticness', 'tempo', 'genres'])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

                for track in tracks:
                    genres = ', '.join(track.genres.values_list('name', flat=True))
                    writer.writerow([track.id, track.title, track.artist.name, track.popularity,
                                     track.energy, track.valence, track.danceability,
                                     track.acousticness, track.tempo, genres])
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)

            response = StreamingHttpResponse(csv_generator(), content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="nexttrack_export.csv"'
            return response

        data = TrackSerializer(tracks, many=True).data
        response = Response(data)
        response['Content-Disposition'] = 'attachment; filename="nexttrack_export.json"'
        return response


    @extend_schema(
        summary="Batch recommendations",
        description="Generate recommendations for multiple playlists in a single request.",
        request=inline_serializer(
            name='BatchRecommendRequest',
            fields={
                'playlists': drf_serializers.ListField(
                    child=drf_serializers.DictField(),
                    help_text="Array of {track_ids, preferences, limit} objects (max 5).",
                ),
            },
        ),
    )
    @action(detail=False, methods=['post'], url_path='recommend/batch', throttle_classes=[ScopedRateThrottle])
    def recommend_batch(self, request: Request) -> Response:
        """Generate recommendations for multiple playlists at once."""
        self.throttle_scope = 'recommend'

        playlists = request.data.get('playlists', [])
        if not playlists or not isinstance(playlists, list):
            return Response({'error': 'playlists must be a non-empty array'}, status=status.HTTP_400_BAD_REQUEST)
        if len(playlists) > 5:
            return Response({'error': 'Maximum 5 playlists per batch request'}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        for i, pl in enumerate(playlists):
            track_ids = pl.get('track_ids', [])
            preferences = pl.get('preferences', {})
            limit = min(max(1, int(pl.get('limit', 10))), 50)
            playlist_id = pl.get('playlist_id', str(i))

            if not track_ids:
                results.append({'playlist_id': playlist_id, 'error': 'track_ids required'})
                continue

            result = get_recommendations_from_sequence(
                track_ids=track_ids, preferences=preferences, limit=limit
            )

            results.append({
                'playlist_id': playlist_id,
                'recommendations': TrackSerializer(result['recommendations'], many=True).data,
                'count': len(result['recommendations']),
            })

        return Response({'results': results})

    @extend_schema(
        summary="Explain why a track was not recommended",
        description="Analyzes why a specific track was excluded from recommendations.",
        request=inline_serializer(
            name='ExplainAbsenceRequest',
            fields={
                'track_id': drf_serializers.CharField(),
                'playlist_track_ids': drf_serializers.ListField(child=drf_serializers.CharField()),
            },
        ),
    )
    @action(detail=False, methods=['post'], url_path='explain-absence')
    def explain_absence(self, request: Request) -> Response:
        """Explain why a track was not included in recommendations."""
        target_id = request.data.get('track_id')
        playlist_ids = request.data.get('playlist_track_ids', [])

        if not target_id or not playlist_ids:
            return Response({'error': 'track_id and playlist_track_ids required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target = Track.objects.select_related('artist').prefetch_related('genres').get(pk=target_id)
        except Track.DoesNotExist:
            return Response({'error': 'Target track not found'}, status=status.HTTP_404_NOT_FOUND)

        playlist_tracks = list(Track.objects.filter(id__in=playlist_ids).select_related('artist').prefetch_related('genres'))
        if not playlist_tracks:
            return Response({'error': 'No valid playlist tracks found'}, status=status.HTTP_404_NOT_FOUND)

        from catalog.services import calculate_centroid
        import numpy as np

        feature_vectors = [get_feature_vector(t) for t in playlist_tracks]
        centroid = calculate_centroid(feature_vectors)
        target_vec = get_feature_vector(target)
        distance = euclidean_distance(centroid, target_vec)

        # Compute average distance of a typical recommendation
        result = get_recommendations_from_sequence(track_ids=playlist_ids, limit=10)
        rec_distances = []
        for rec in result.get('recommendations', []):
            rv = get_feature_vector(rec)
            rec_distances.append(euclidean_distance(centroid, rv))
        avg_rec_distance = np.mean(rec_distances) if rec_distances else 0

        features = ['valence', 'energy', 'danceability', 'acousticness', 'tempo']
        feature_gaps = {}
        for i, feat in enumerate(features):
            c_val = centroid[i]
            t_val = target_vec[i]
            gap = abs(c_val - t_val)
            feature_gaps[feat] = {
                'centroid': round(float(c_val), 3),
                'track': round(float(t_val), 3),
                'gap': round(float(gap), 3),
            }

        # Check genre overlap
        playlist_genres = set()
        for t in playlist_tracks:
            playlist_genres.update(t.genres.values_list('name', flat=True))
        target_genres = set(target.genres.values_list('name', flat=True))
        genre_overlap = playlist_genres & target_genres

        reasons = []
        if distance > avg_rec_distance * 1.5:
            reasons.append(f"Track is too far from playlist centroid (distance: {distance:.3f} vs avg recommendation: {avg_rec_distance:.3f})")

        biggest_gap = max(feature_gaps.items(), key=lambda x: x[1]['gap'])
        if biggest_gap[1]['gap'] > 0.3:
            reasons.append(f"Biggest feature gap is in {biggest_gap[0]}: playlist avg {biggest_gap[1]['centroid']:.2f} vs track {biggest_gap[1]['track']:.2f}")

        if not genre_overlap:
            reasons.append(f"No genre overlap (track genres: {list(target_genres)[:5]}, playlist genres: {list(playlist_genres)[:5]})")

        if not reasons:
            reasons.append("Track is close enough to the centroid — it may appear with different playlist compositions or higher limit.")

        return Response({
            'track': TrackSerializer(target).data,
            'distance': round(float(distance), 4),
            'avg_recommendation_distance': round(float(avg_rec_distance), 4),
            'feature_gaps': feature_gaps,
            'genre_overlap': list(genre_overlap),
            'reasons': reasons,
        })

    @extend_schema(
        summary="Generate mood journey playlist",
        description="Creates a playlist transitioning from one mood to another using feature interpolation.",
        request=inline_serializer(
            name='MoodJourneyRequest',
            fields={
                'start': drf_serializers.DictField(help_text="Start mood features {valence, energy, danceability, acousticness, tempo}"),
                'end': drf_serializers.DictField(help_text="End mood features"),
                'steps': drf_serializers.IntegerField(required=False, default=8),
            },
        ),
    )
    @action(detail=False, methods=['post'], url_path='mood-journey')
    def mood_journey(self, request: Request) -> Response:
        """Generate a playlist that transitions between two moods."""
        from catalog.services import generate_mood_journey

        start = request.data.get('start', {})
        end = request.data.get('end', {})
        steps = min(max(3, int(request.data.get('steps', 8))), 15)

        if not start or not end:
            return Response({'error': 'start and end mood features required'}, status=status.HTTP_400_BAD_REQUEST)

        journey = generate_mood_journey(start, end, steps=steps)

        results = []
        for step in journey:
            track_data = TrackSerializer(step['track']).data
            track_data['journey_step'] = step['step']
            track_data['target_features'] = step['target']
            track_data['distance'] = step['distance']
            results.append(track_data)

        return Response({
            'journey': results,
            'steps': steps,
            'start_mood': start,
            'end_mood': end,
        })

    @extend_schema(
        summary="Surprise Me - random walk discovery",
        description="Generates a chain of tracks where each track seeds the next, creating an unpredictable discovery path.",
        request=inline_serializer(
            name='SurpriseRequest',
            fields={
                'seed_track_id': drf_serializers.CharField(),
                'walk_length': drf_serializers.IntegerField(required=False, default=5),
                'serendipity': drf_serializers.FloatField(required=False, default=0.5),
            },
        ),
    )
    @action(detail=False, methods=['post'], url_path='surprise')
    def surprise(self, request: Request) -> Response:
        """Generate a random walk discovery chain from a seed track."""
        from catalog.services import random_walk_recommendations

        seed_id = request.data.get('seed_track_id')
        if not seed_id:
            return Response({'error': 'seed_track_id required'}, status=status.HTTP_400_BAD_REQUEST)

        walk_length = min(max(3, int(request.data.get('walk_length', 5))), 15)
        serendipity = min(max(0.1, float(request.data.get('serendipity', 0.5))), 1.0)

        walk = random_walk_recommendations(seed_id, walk_length, serendipity)

        if not walk:
            return Response({'error': 'Seed track not found'}, status=status.HTTP_404_NOT_FOUND)

        results = []
        for step in walk:
            data = TrackSerializer(step['track']).data
            data['walk_step'] = step['step']
            data['distance'] = step['distance']
            data['feature_shift'] = step['shift']
            results.append(data)

        return Response({'walk': results, 'walk_length': walk_length, 'serendipity': serendipity})

    @extend_schema(
        summary="Activity-based playlist",
        description="Generate a playlist tailored to an activity (running, study, party, yoga).",
        request=inline_serializer(
            name='ActivityPlaylistRequest',
            fields={
                'activity': drf_serializers.ChoiceField(choices=['running', 'study', 'party', 'yoga']),
                'seed_track_ids': drf_serializers.ListField(child=drf_serializers.CharField(), required=False),
            },
        ),
    )
    @action(detail=False, methods=['post'], url_path='activity-playlist')
    def activity_playlist(self, request: Request) -> Response:
        """Generate a structured playlist for an activity."""
        from catalog.services import generate_activity_playlist, ACTIVITY_PRESETS

        activity = request.data.get('activity')
        if not activity or activity not in ACTIVITY_PRESETS:
            return Response({
                'error': f'activity required. Options: {list(ACTIVITY_PRESETS.keys())}'
            }, status=status.HTTP_400_BAD_REQUEST)

        seed_ids = request.data.get('seed_track_ids', [])
        result = generate_activity_playlist(activity, seed_ids)

        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        # Serialize tracks
        for phase in result['phases']:
            phase['tracks'] = TrackSerializer(phase['tracks'], many=True).data

        return Response(result)

    @action(detail=False, methods=['get'], url_path='scatter-data')
    def scatter_data(self, request: Request) -> Response:
        """Return track data for scatter plot visualization."""
        x_axis = request.query_params.get('x', 'valence')
        y_axis = request.query_params.get('y', 'energy')
        valid_axes = ['valence', 'energy', 'danceability', 'acousticness', 'tempo', 'popularity']

        if x_axis not in valid_axes or y_axis not in valid_axes:
            return Response({'error': f'Invalid axis. Choose from: {valid_axes}'}, status=400)

        genre_filter = request.query_params.get('genre')
        limit = min(int(request.query_params.get('limit', 500)), 1000)

        qs = Track.objects.filter(is_audio_analyzed=True)
        if genre_filter:
            qs = qs.filter(genres__name__icontains=genre_filter)

        tracks = qs.order_by('-popularity')[:limit].values(
            'id', 'title', 'artist__name', x_axis, y_axis, 'popularity'
        )

        return Response({
            'x_axis': x_axis,
            'y_axis': y_axis,
            'count': len(tracks),
            'tracks': list(tracks),
        })

    @action(detail=False, methods=['get'], url_path='artist-origins')
    def artist_origins(self, request: Request) -> Response:
        """Return artist origin data for world map visualization."""
        from django.db.models import Count

        origins = (
            Artist.objects.exclude(origin_country__isnull=True)
            .exclude(origin_country='')
            .values('origin_country')
            .annotate(count=Count('id'), avg_popularity=models.Avg('popularity'))
            .order_by('-count')
        )

        return Response({
            'total_artists_with_origin': sum(o['count'] for o in origins),
            'countries': list(origins),
        })

    @extend_schema(
        summary="Genre lineage map data",
        description=(
            "Returns genre co-occurrence graph data for visualization. "
            "Nodes represent genres (sized by track count), edges represent "
            "co-occurrences across tracks (weighted by shared track count, min 2)."
        ),
        responses={200: inline_serializer(
            name='GenreLineageData',
            fields={
                'nodes': drf_serializers.ListField(),
                'edges': drf_serializers.ListField(),
            },
        )},
    )
    @action(detail=False, methods=['get'], url_path='genre-lineage', throttle_classes=[ScopedRateThrottle])
    def genre_lineage(self, request: Request) -> Response:
        """Return genre co-occurrence graph data for the lineage map."""
        self.throttle_scope = 'statistics'

        cache_key = "genre_lineage_data"
        cached = safe_cache_get(cache_key)
        if cached:
            return Response(cached)

        data = get_genre_lineage_data()
        safe_cache_set(cache_key, data, timeout=3600)  # 1h cache

        return Response(data)


class SurveyAPIView(APIView):
    """Handles survey submissions for user feedback."""

    @extend_schema(
        summary="Submit user survey",
        description="Save a user experience survey with satisfaction, discovery, and accuracy ratings (1-5).",
        request=inline_serializer(
            name='SurveyRequest',
            fields={
                'overall_satisfaction': drf_serializers.IntegerField(min_value=1, max_value=5),
                'discovery_rating': drf_serializers.IntegerField(min_value=1, max_value=5),
                'accuracy_rating': drf_serializers.IntegerField(min_value=1, max_value=5),
                'liked_most': drf_serializers.CharField(required=False),
                'improvement_suggestion': drf_serializers.CharField(required=False),
                'would_recommend': drf_serializers.BooleanField(required=False, default=True),
                'tracks_interacted': drf_serializers.IntegerField(required=False, default=0),
            },
        ),
        responses={201: inline_serializer(
            name='SurveyResponse',
            fields={
                'status': drf_serializers.CharField(),
                'survey_id': drf_serializers.IntegerField(),
                'message': drf_serializers.CharField(),
            },
        )},
    )
    def post(self, request: Request) -> Response:
        """Save a new survey response."""
        required_fields = ['overall_satisfaction', 'discovery_rating', 'accuracy_rating']

        for field in required_fields:
            if field not in request.data:
                return Response(
                    {'error': f'{field} is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            value = request.data[field]
            if not isinstance(value, int) or value < 1 or value > 5:
                return Response(
                    {'error': f'{field} must be an integer between 1 and 5'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key

        survey = UserSurvey.objects.create(
            overall_satisfaction=request.data['overall_satisfaction'],
            discovery_rating=request.data['discovery_rating'],
            accuracy_rating=request.data['accuracy_rating'],
            liked_most=request.data.get('liked_most', ''),
            improvement_suggestion=request.data.get('improvement_suggestion', ''),
            would_recommend=request.data.get('would_recommend', True),
            tracks_interacted=request.data.get('tracks_interacted', 0),
            session_key=session_key
        )

        AnalyticsEvent.objects.create(
            event_type='survey_completed',
            session_key=session_key,
            metadata={
                'survey_id': survey.id,
                'average_score': survey.average_score
            }
        )

        return Response({
            'status': 'created',
            'survey_id': survey.id,
            'message': 'Thank you for your feedback!'
        }, status=status.HTTP_201_CREATED)


class AnalyticsAPIView(APIView):
    """Records user interaction events for analytics."""

    @extend_schema(
        summary="Record analytics event",
        description="Log a user interaction event (search, recommend, play, like, dislike, etc.).",
        request=inline_serializer(
            name='AnalyticsEventRequest',
            fields={
                'event_type': drf_serializers.ChoiceField(
                    choices=['search', 'recommend', 'play', 'like', 'dislike',
                             'add_playlist', 'filter_applied', 'survey_completed'],
                ),
                'track_id': drf_serializers.CharField(required=False),
                'metadata': drf_serializers.DictField(required=False),
            },
        ),
        responses={201: inline_serializer(
            name='AnalyticsEventResponse',
            fields={'status': drf_serializers.CharField()},
        )},
    )
    def post(self, request: Request) -> Response:
        """Save an analytics event."""
        event_type = request.data.get('event_type')
        valid_types = [choice[0] for choice in AnalyticsEvent.EVENT_TYPES]

        if not event_type or event_type not in valid_types:
            return Response(
                {'error': f'event_type must be one of: {valid_types}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not request.session.session_key:
            request.session.create()

        AnalyticsEvent.objects.create(
            event_type=event_type,
            session_key=request.session.session_key,
            track_id=request.data.get('track_id'),
            metadata=request.data.get('metadata')
        )

        return Response({'status': 'recorded'}, status=status.HTTP_201_CREATED)


class RecommendationMetricsView(APIView):
    """Exposes recommendation quality metrics for monitoring."""

    @extend_schema(
        summary="Recommendation quality metrics",
        description="Returns quality metrics: average distance, diversity, feature coverage, feedback ratios.",
    )
    def get(self, request: Request) -> Response:
        from catalog.models import RecommendationFeedback
        from django.db.models import Avg, Count, Q
        from datetime import timedelta
        from django.utils import timezone

        cache_key = "rec_quality_metrics"
        cached = safe_cache_get(cache_key)
        if cached:
            return Response(cached)

        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)

        # Feedback quality
        recent_feedback = RecommendationFeedback.objects.filter(created_at__gte=seven_days_ago)
        total_feedback = recent_feedback.count()
        likes = recent_feedback.filter(score=True).count()
        dislikes = total_feedback - likes
        like_ratio = round(likes / total_feedback * 100, 1) if total_feedback > 0 else 0

        # Library coverage
        total_tracks = Track.objects.count()
        analyzed_tracks = Track.objects.filter(is_audio_analyzed=True).count()
        analysis_coverage = round(analyzed_tracks / total_tracks * 100, 1) if total_tracks > 0 else 0

        # Artist diversity in recent recommendations
        recent_events = AnalyticsEvent.objects.filter(
            event_type='recommend',
            created_at__gte=seven_days_ago,
        ).count()

        # Genre distribution
        genre_stats = list(
            Genre.objects.annotate(track_count=Count('tracks'))
            .filter(track_count__gt=0)
            .values('name', 'track_count')
            .order_by('-track_count')[:10]
        )

        # Survey satisfaction (if available)
        recent_surveys = UserSurvey.objects.filter(created_at__gte=thirty_days_ago)
        avg_satisfaction = recent_surveys.aggregate(avg=Avg('overall_satisfaction'))['avg']
        avg_accuracy = recent_surveys.aggregate(avg=Avg('accuracy_rating'))['avg']

        metrics = {
            'feedback': {
                'total_7d': total_feedback,
                'likes': likes,
                'dislikes': dislikes,
                'like_ratio_pct': like_ratio,
            },
            'library': {
                'total_tracks': total_tracks,
                'analyzed_tracks': analyzed_tracks,
                'analysis_coverage_pct': analysis_coverage,
                'total_artists': Artist.objects.count(),
                'total_genres': Genre.objects.count(),
            },
            'activity': {
                'recommendations_7d': recent_events,
                'surveys_30d': recent_surveys.count(),
            },
            'quality': {
                'avg_satisfaction': round(float(avg_satisfaction), 2) if avg_satisfaction else None,
                'avg_accuracy': round(float(avg_accuracy), 2) if avg_accuracy else None,
            },
            'top_genres': genre_stats,
            'generated_at': now.isoformat(),
        }

        safe_cache_set(cache_key, metrics, timeout=1800)  # 30 min cache
        return Response(metrics)


class FeedbackAPIView(APIView):
    """DRF endpoint for track feedback (like/dislike) with throttling."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'feedback'

    @extend_schema(
        summary="Submit track feedback",
        description="Like or dislike a recommended track. Toggle: submitting the same score removes feedback.",
        request=inline_serializer(
            name='FeedbackRequest',
            fields={
                'track_id': drf_serializers.CharField(),
                'score': drf_serializers.BooleanField(help_text="True=like, False=dislike"),
            },
        ),
        responses={200: inline_serializer(
            name='FeedbackResponse',
            fields={
                'status': drf_serializers.CharField(),
                'track_id': drf_serializers.CharField(),
                'score': drf_serializers.CharField(),
                'message': drf_serializers.CharField(),
            },
        )},
    )
    def post(self, request: Request) -> Response:
        """Submit feedback for a track."""
        from catalog.models import RecommendationFeedback

        track_id = request.data.get('track_id')
        score = request.data.get('score')

        if not track_id:
            return Response({'error': 'track_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            track = Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            return Response({'error': 'Track not found'}, status=status.HTTP_404_NOT_FOUND)

        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key

        new_score = bool(score)

        existing = RecommendationFeedback.objects.filter(
            track=track, session_key=session_key
        ).first()

        if existing:
            if existing.score == new_score:
                existing.delete()
                return Response({
                    'status': 'removed',
                    'track_id': track_id,
                    'score': 'like' if new_score else 'dislike',
                    'message': 'Feedback removed (toggle)'
                })
            else:
                existing.score = new_score
                existing.save()
                return Response({
                    'status': 'updated',
                    'feedback_id': existing.id,
                    'track_id': track_id,
                    'score': 'like' if new_score else 'dislike',
                    'message': 'Feedback updated'
                })

        feedback = RecommendationFeedback.objects.create(
            track=track, score=new_score, session_key=session_key
        )
        return Response({
            'status': 'created',
            'feedback_id': feedback.id,
            'track_id': track_id,
            'score': 'like' if new_score else 'dislike',
            'message': 'Feedback saved'
        }, status=status.HTTP_201_CREATED)
