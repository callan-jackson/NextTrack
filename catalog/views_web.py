"""Web views for the frontend pages (home, builder, results, etc.)."""

import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views import View
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect

from catalog.models import Genre, Track, RecommendationFeedback, UserSurvey, AnalyticsEvent, SharedPlaylist
from catalog.services import (
    search_tracks,
    get_recommendations_from_sequence,
    get_enhanced_recommendations,
    get_available_filters,
    get_influence_recommendations,
    calculate_categorical_preferences,
    apply_categorical_preferences,
    apply_external_data_enhancements,
    get_influence_based_suggestions,
    calculate_diversity_from_external_data,
    COUNTRY_NAMES
)
from catalog.external_data import get_live_external_service
from catalog.forms import SearchForm, PreferenceForm, FeedbackForm
from catalog.spotify_oauth import SpotifyUserClient, SpotifyOAuthError

logger = logging.getLogger(__name__)


def _log_analytics(request, event_type, track_id=None, metadata=None):
    """Helper to log an analytics event for the current session."""
    session_key = request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key
    AnalyticsEvent.objects.create(
        event_type=event_type,
        session_key=session_key,
        track_id=track_id,
        metadata=metadata or {},
    )


@require_POST
def centroid_preview(request: HttpRequest) -> JsonResponse:
    """Return average audio features for a set of track IDs."""
    try:
        data = json.loads(request.body)
        track_ids = data.get('track_ids', [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not track_ids:
        return JsonResponse({'error': 'No track IDs provided'}, status=400)

    tracks = Track.objects.filter(id__in=track_ids)
    if not tracks.exists():
        return JsonResponse({'error': 'No tracks found'}, status=404)

    total = tracks.count()
    return JsonResponse({
        'energy': sum(t.energy for t in tracks) / total,
        'valence': sum(t.valence for t in tracks) / total,
        'danceability': sum(t.danceability for t in tracks) / total,
        'acousticness': sum(t.acousticness for t in tracks) / total,
        'tempo': sum(t.tempo for t in tracks) / total,
    })


def normalize_vector(vector_dict):
    """Normalize audio features to 0-1 range so the radar chart works."""
    normalized = {}

    for key, value in vector_dict.items():
        if value is None:
            normalized[key] = 0.0
            continue

        if key == 'tempo':
            normalized['tempo_normalized'] = min(float(value) / 200.0, 1.0)
        elif key == 'loudness':
            normalized['loudness_normalized'] = max((float(value) + 60.0) / 60.0, 0.0)
        elif key in ('duration_ms', 'duration'):
            continue
        else:
            normalized[key] = max(0.0, min(float(value), 1.0))

    return normalized


class HomeView(View):
    """Homepage with search. Searches local DB and Spotify API."""
    template_name = 'catalog/home.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        query = request.GET.get('q', '')
        results = []

        if query and len(query) >= 2:
            results = search_tracks(query, limit=20)
            _log_analytics(request, 'search', metadata={'query': query, 'results': len(results)})

        playlist_ids = request.session.get('playlist', [])
        playlist_count = len(playlist_ids)

        return render(request, self.template_name, {
            'query': query,
            'results': results,
            'playlist_count': playlist_count,
        })


class PlaylistBuilderView(View):
    """Playlist building page. Tracks stored in session (max 20)."""
    template_name = 'catalog/builder.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        playlist_ids = request.session.get('playlist', [])

        playlist_tracks = []
        if playlist_ids:
            tracks = Track.objects.filter(id__in=playlist_ids).select_related('artist')
            track_map = {t.id: t for t in tracks}
            playlist_tracks = [track_map[tid] for tid in playlist_ids if tid in track_map]

        return render(request, self.template_name, {
            'playlist_tracks': playlist_tracks,
            'playlist_count': len(playlist_tracks),
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        """Handle add/remove/clear actions on the playlist."""
        action = request.POST.get('action')
        track_id = request.POST.get('track_id')

        if 'playlist' not in request.session:
            request.session['playlist'] = []

        playlist = request.session['playlist']

        if action == 'add' and track_id:
            if len(playlist) < 100:
                if Track.objects.filter(id=track_id).exists():
                    playlist.append(track_id)
                    request.session['playlist'] = playlist
                    request.session.modified = True

        elif action == 'remove' and track_id:
            if track_id in playlist:
                playlist.remove(track_id)
                request.session['playlist'] = playlist
                request.session.modified = True

        elif action == 'clear':
            request.session['playlist'] = []
            request.session.modified = True

        next_url = request.POST.get('next', '/builder/')
        return redirect(next_url)


class RecommendationsView(View):
    """Shows recommendations with filters, explanations, and radar chart."""
    template_name = 'catalog/results.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        playlist_ids = request.session.get('playlist', [])

        if not playlist_ids:
            return render(request, self.template_name, {
                'error': 'Your playlist is empty. Add some tracks first!',
                'recommendations': [],
                'input_tracks': [],
                'centroid': {},
                'input_vibe_json': '{}',
                'result_vibe_json': '{}',
                'preference_form': PreferenceForm(),
                'available_filters': {},
                'explanations': {},
                'diversity_stats': {},
            })

        preference_form = PreferenceForm(request.GET or None)
        preferences = {}

        if preference_form.is_valid():
            preferences = preference_form.get_preferences()

        country_filter = request.GET.get('country', None)
        decade_filter = request.GET.get('decade', None)
        artist_type_filter = request.GET.get('artist_type', None)
        exclude_unanalyzed = request.GET.get('analyzed_only', '').lower() == 'true'

        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        result = get_enhanced_recommendations(
            track_ids=playlist_ids,
            preferences=preferences if preferences else None,
            limit=15,
            session_key=session_key,
            country_filter=country_filter,
            decade_filter=decade_filter,
            artist_type_filter=artist_type_filter,
            exclude_unanalyzed=exclude_unanalyzed,
            include_explanations=True
        )

        recommendations = result['recommendations']

        _log_analytics(request, 'recommend', metadata={
            'input_tracks': len(playlist_ids),
            'results': len(recommendations),
        })

        artist_info, input_artist_info, external_service = self._fetch_external_artist_data(
            recommendations, result['input_tracks']
        )

        recommendations, categorical_prefs, categorical_adjustments, categorical_learning_applied = (
            self._apply_preference_learning(recommendations, session_key, artist_info, external_service)
        )

        recommendations, external_enhancements, external_enhancement_applied, influence_suggestions = (
            self._apply_external_enhancements(recommendations, result.get('input_tracks', []), external_service)
        )

        context = self._build_template_context(
            result, recommendations, preferences, preference_form,
            playlist_ids, artist_info, input_artist_info,
            categorical_prefs, categorical_adjustments, categorical_learning_applied,
            external_enhancements, external_enhancement_applied, influence_suggestions,
        )

        return render(request, self.template_name, context)

    def _fetch_external_artist_data(self, recommendations, input_tracks):
        """Fetch external artist data to enrich recommendations and input tracks."""
        artist_info = {}
        input_artist_info = {}
        external_service = None

        if not recommendations:
            return artist_info, input_artist_info, external_service

        external_data = {}
        try:
            rec_artist_names = list(set(track.artist.name for track in recommendations))
            input_artist_names = list(set(track.artist.name for track in input_tracks))

            external_service = get_live_external_service()
            external_data = external_service.batch_get_artist_info(
                rec_artist_names + input_artist_names,
                max_live_fetches=5
            )
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(f"External data fetch failed: {e}")
            external_data = {}

        for track in recommendations:
            ext = external_data.get(track.artist.name, {})
            data_sources = ext.get('data_sources', [])
            artist_info[track.id] = {
                'name': track.artist.name,
                'is_enriched': ext.get('source') == 'external' and (ext.get('country') or data_sources),
                'country': ext.get('country'),
                'country_name': COUNTRY_NAMES.get(ext.get('country'), ext.get('country')),
                'type': ext.get('type'),
                'formed_year': ext.get('formed_year'),
                'decade': f"{(ext['formed_year'] // 10) * 10}s" if ext.get('formed_year') else None,
                'description': ext.get('description'),
                'tags': ext.get('tags', [])[:5],
                'similar_artists': ext.get('similar_artists', [])[:5],
                'influenced_by': ext.get('influenced_by', [])[:5],
                'lastfm_tags': ext.get('lastfm_tags', [])[:8],
                'listeners': ext.get('listeners', 0),
                'genius_url': ext.get('genius_url'),
                'data_sources': data_sources,
            }

        for track in input_tracks:
            ext = external_data.get(track.artist.name, {})
            input_artist_info[track.id] = {
                'name': track.artist.name,
                'is_enriched': ext.get('source') == 'external' and (ext.get('country') or ext.get('data_sources')),
                'country': ext.get('country'),
                'type': ext.get('type'),
                'tags': ext.get('tags', [])[:5],
                'similar_artists': ext.get('similar_artists', [])[:3],
                'influenced_by': ext.get('influenced_by', [])[:3],
            }

        return artist_info, input_artist_info, external_service

    def _apply_preference_learning(self, recommendations, session_key, artist_info, external_service):
        """Re-rank recommendations based on learned categorical preferences."""
        categorical_prefs = {}
        categorical_adjustments = {}
        categorical_learning_applied = False

        try:
            categorical_prefs = calculate_categorical_preferences(
                session_key,
                external_data_service=external_service
            )

            if categorical_prefs.get('has_preferences') and recommendations:
                artist_info_map = {}
                for track in recommendations:
                    ext = artist_info.get(track.id, {})
                    artist_info_map[track.id] = {
                        'country': ext.get('country'),
                        'type': ext.get('type'),
                        'decade': ext.get('decade'),
                    }

                rec_with_scores = [(track, 1.0 / (i + 1)) for i, track in enumerate(recommendations)]

                adjusted_recs = apply_categorical_preferences(
                    rec_with_scores,
                    categorical_prefs,
                    artist_info_map
                )

                recommendations = [track for track, score, adjustments in adjusted_recs]
                for track, score, adjustments in adjusted_recs:
                    if adjustments:
                        boosts = sum(1 for a in adjustments if a.startswith('+'))
                        penalties = sum(1 for a in adjustments if a.startswith('-'))
                        categorical_adjustments[track.id] = {
                            'reasons': adjustments,
                            'total_adjustment': boosts - penalties,
                            'boosts': boosts,
                            'penalties': penalties
                        }
                        categorical_learning_applied = True
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(f"Categorical preference learning failed: {e}")

        return recommendations, categorical_prefs, categorical_adjustments, categorical_learning_applied

    def _apply_external_enhancements(self, recommendations, input_tracks, external_service):
        """Boost recommendations based on similar artists / influence relationships."""
        external_enhancements = {}
        external_enhancement_applied = False
        influence_suggestions = []

        try:
            if recommendations and input_tracks:
                playlist_artist_names = {t.artist.name for t in input_tracks}
                rec_with_scores = [(track, 1.0 / (i + 1)) for i, track in enumerate(recommendations)]

                enhanced_recs = apply_external_data_enhancements(
                    rec_with_scores,
                    playlist_artist_names,
                    external_data_service=external_service
                )

                recommendations = [track for track, score, enhancements in enhanced_recs]
                for track, score, enhancements in enhanced_recs:
                    if enhancements.get('total_boost', 0) > 0:
                        external_enhancements[track.id] = enhancements
                        external_enhancement_applied = True

                influence_suggestions = get_influence_based_suggestions(
                    playlist_artist_names,
                    external_service,
                    limit=5
                )
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(f"External data enhancements failed: {e}")

        return recommendations, external_enhancements, external_enhancement_applied, influence_suggestions

    def _build_template_context(
        self, result, recommendations, preferences, preference_form,
        playlist_ids, artist_info, input_artist_info,
        categorical_prefs, categorical_adjustments, categorical_learning_applied,
        external_enhancements, external_enhancement_applied, influence_suggestions,
    ):
        """Assemble the template context dict for the results page."""
        available_filters = get_available_filters(playlist_ids)

        input_vibe = result['centroid']
        result_vibe = self._calculate_average_features(recommendations)

        normalized_input = normalize_vector(input_vibe)
        normalized_result = normalize_vector(result_vibe)

        input_vibe_json = json.dumps({
            'danceability': round(normalized_input.get('danceability', 0), 3),
            'energy': round(normalized_input.get('energy', 0), 3),
            'valence': round(normalized_input.get('valence', 0), 3),
            'acousticness': round(normalized_input.get('acousticness', 0), 3),
            'tempo_normalized': round(normalized_input.get('tempo_normalized', 0), 3),
        })

        result_vibe_json = json.dumps({
            'danceability': round(normalized_result.get('danceability', 0), 3),
            'energy': round(normalized_result.get('energy', 0), 3),
            'valence': round(normalized_result.get('valence', 0), 3),
            'acousticness': round(normalized_result.get('acousticness', 0), 3),
            'tempo_normalized': round(normalized_result.get('tempo_normalized', 0), 3),
        })

        explanations_json = json.dumps(result.get('explanations', {}))

        active_filters = result.get('filters_applied', {})
        active_filters_display = []
        if 'country' in active_filters:
            active_filters_display.append({
                'type': 'country',
                'value': active_filters['country'],
                'label': COUNTRY_NAMES.get(active_filters['country'], active_filters['country'])
            })
        if 'region' in active_filters:
            active_filters_display.append({
                'type': 'region',
                'value': active_filters['region'],
                'label': active_filters['region'].replace('_', ' ').title()
            })
        if 'decade' in active_filters:
            active_filters_display.append({
                'type': 'decade',
                'value': active_filters['decade'],
                'label': f"{active_filters['decade']}s"
            })
        if 'artist_type' in active_filters:
            active_filters_display.append({
                'type': 'artist_type',
                'value': active_filters['artist_type'],
                'label': active_filters['artist_type']
            })

        diversity_stats = calculate_diversity_from_external_data(recommendations, artist_info)

        return {
            'recommendations': recommendations,
            'input_tracks': result['input_tracks'],
            'centroid': input_vibe,
            'result_vibe': result_vibe,
            'preferences': preferences,
            'input_vibe_json': input_vibe_json,
            'result_vibe_json': result_vibe_json,
            'preference_form': preference_form,
            'available_filters': available_filters,
            'active_filters': active_filters_display,
            'explanations': result.get('explanations', {}),
            'explanations_json': explanations_json,
            'diversity_stats': diversity_stats,
            'feedback_applied': result.get('feedback_applied', False),
            'no_results_message': result.get('message', ''),
            'artist_info': artist_info,
            'input_artist_info': input_artist_info,
            'categorical_learning_applied': categorical_learning_applied,
            'categorical_prefs': categorical_prefs,
            'categorical_adjustments': categorical_adjustments,
            'external_enhancement_applied': external_enhancement_applied,
            'external_enhancements': external_enhancements,
            'influence_suggestions': influence_suggestions,
        }

    def post(self, request: HttpRequest) -> HttpResponse:
        """Validate preference form and redirect to GET with query params."""
        preference_form = PreferenceForm(request.POST)

        if preference_form.is_valid():
            preferences = preference_form.get_preferences()
            query_string = '&'.join(f'{k}={v}' for k, v in preferences.items())
            return redirect(f'/results/?{query_string}')

        playlist_ids = request.session.get('playlist', [])
        return render(request, self.template_name, {
            'error': 'Invalid preference values. Please enter values between 0 and 1.',
            'preference_form': preference_form,
            'recommendations': [],
            'input_tracks': [],
            'centroid': {},
            'input_vibe_json': '{}',
            'result_vibe_json': '{}',
        })

    def _calculate_average_features(self, tracks):
        """Average the audio features across tracks for the radar chart."""
        if not tracks:
            return {
                'danceability': 0,
                'energy': 0,
                'valence': 0,
                'acousticness': 0,
                'tempo': 0,
                'loudness': 0,
            }

        total = len(tracks)
        return {
            'danceability': sum(t.danceability for t in tracks) / total,
            'energy': sum(t.energy for t in tracks) / total,
            'valence': sum(t.valence for t in tracks) / total,
            'acousticness': sum(t.acousticness for t in tracks) / total,
            'tempo': sum(t.tempo for t in tracks) / total,
            'loudness': sum(t.loudness for t in tracks) / total,
        }



@require_POST
def add_to_playlist_ajax(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint to add a track to the session playlist."""

    if request.content_type and 'application/json' in request.content_type:
        try:
            data = json.loads(request.body)
            track_id = data.get('track_id')
            force_add = data.get('force_add', False)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        track_id = request.POST.get('track_id')
        force_add = request.POST.get('force_add', '').lower() == 'true'

    if not track_id:
        return JsonResponse({'error': 'track_id required'}, status=400)

    try:
        track = Track.objects.select_related('artist').get(id=track_id)
    except Track.DoesNotExist:
        return JsonResponse({'error': 'Track not found'}, status=404)

    if 'playlist' not in request.session:
        request.session['playlist'] = []

    playlist = request.session['playlist']

    if track_id in playlist and not force_add:
        return JsonResponse({
            'status': 'duplicate',
            'count': len(playlist),
            'track_title': track.title,
            'track_artist': track.artist.name,
            'message': 'This song is already in your playlist'
        })

    if len(playlist) >= 100:
        return JsonResponse({
            'status': 'playlist_full',
            'count': len(playlist),
            'message': 'Playlist is full (maximum 100 tracks)'
        })

    playlist.append(track_id)
    request.session['playlist'] = playlist
    request.session.modified = True

    _log_analytics(request, 'add_playlist', track_id=track_id)

    return JsonResponse({
        'status': 'added',
        'count': len(playlist),
        'track_title': track.title,
        'track_artist': track.artist.name,
        'message': f'Added "{track.title}" to playlist'
    })



@require_POST
def submit_feedback(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint for like/dislike feedback with toggle support."""
    try:
        if request.content_type and 'application/json' in request.content_type:
            data = json.loads(request.body)
            track_id = data.get('track_id')
            score = data.get('score')
        else:
            track_id = request.POST.get('track_id')
            score = request.POST.get('score')

        if isinstance(score, bool):
            score_int = 1 if score else -1
        elif isinstance(score, int):
            score_int = score
        elif isinstance(score, str):
            if score.lower() in ('true', '1', 'like'):
                score_int = 1
            elif score.lower() in ('false', '-1', 'dislike'):
                score_int = -1
            else:
                score_int = 0
        else:
            score_int = 0

        if not track_id or not str(track_id).strip():
            return JsonResponse({'error': 'Track ID is required'}, status=400)

        track_id = str(track_id).strip()

        try:
            track = Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            return JsonResponse({'error': 'Track not found'}, status=404)

        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        existing_feedback = RecommendationFeedback.objects.filter(
            track=track,
            session_key=session_key
        ).first()

        if score_int == 0:
            if existing_feedback:
                existing_feedback.delete()
                return JsonResponse({
                    'status': 'removed',
                    'track_id': track_id,
                    'message': 'Feedback removed'
                })
            else:
                return JsonResponse({
                    'status': 'none',
                    'track_id': track_id,
                    'message': 'No feedback to remove'
                })

        new_score = (score_int == 1)

        if existing_feedback:
            if existing_feedback.score == new_score:
                existing_feedback.delete()
                return JsonResponse({
                    'status': 'removed',
                    'track_id': track_id,
                    'score': 'like' if new_score else 'dislike',
                    'message': 'Feedback removed (undo)'
                })
            else:
                existing_feedback.score = new_score
                existing_feedback.save()
                return JsonResponse({
                    'status': 'updated',
                    'feedback_id': existing_feedback.id,
                    'track_id': track_id,
                    'score': 'like' if new_score else 'dislike',
                    'message': 'Feedback updated'
                })
        else:
            feedback = RecommendationFeedback.objects.create(
                track=track,
                score=new_score,
                session_key=session_key
            )
            _log_analytics(request, 'like' if new_score else 'dislike', track_id=track_id)
            return JsonResponse({
                'status': 'created',
                'feedback_id': feedback.id,
                'track_id': track_id,
                'score': 'like' if new_score else 'dislike',
                'message': 'Feedback saved'
            })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        import traceback
        logger.error(f"Feedback error: {e}\n{traceback.format_exc()}")
        return JsonResponse({'error': str(e)}, status=500)


class FeedbackListView(View):
    """Shows the user's feedback history with undo support."""
    template_name = 'catalog/feedback_list.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        """Display feedback history for this session."""
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        feedback_list = RecommendationFeedback.objects.filter(
            session_key=session_key
        ).select_related('track', 'track__artist').order_by('-created_at')

        total_feedback = feedback_list.count()
        likes = feedback_list.filter(score=True).count()
        dislikes = total_feedback - likes

        return render(request, self.template_name, {
            'feedback_list': feedback_list,
            'total_feedback': total_feedback,
            'likes': likes,
            'dislikes': dislikes,
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        """Delete feedback entry (only if it belongs to this session)."""
        action = request.POST.get('action')
        feedback_id = request.POST.get('feedback_id')

        if action == 'delete' and feedback_id:
            session_key = request.session.session_key

            try:
                feedback = RecommendationFeedback.objects.get(
                    id=feedback_id,
                    session_key=session_key
                )
                feedback.delete()
            except RecommendationFeedback.DoesNotExist:
                pass

        return redirect('feedback_history')


class AnalyticsDashboardView(View):
    """Dashboard showing survey stats, event counts, and feedback trends."""
    template_name = 'catalog/analytics_dashboard.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the analytics dashboard."""
        from django.db.models import Avg, Count
        from django.db.models.functions import TruncDate
        from datetime import timedelta
        from django.utils import timezone

        surveys = UserSurvey.objects.all()
        survey_stats = {
            'total_responses': surveys.count(),
            'avg_satisfaction': surveys.aggregate(avg=Avg('overall_satisfaction'))['avg'] or 0,
            'avg_discovery': surveys.aggregate(avg=Avg('discovery_rating'))['avg'] or 0,
            'avg_accuracy': surveys.aggregate(avg=Avg('accuracy_rating'))['avg'] or 0,
            'would_recommend_pct': 0,
        }

        if survey_stats['total_responses'] > 0:
            recommend_count = surveys.filter(would_recommend=True).count()
            survey_stats['would_recommend_pct'] = round(
                (recommend_count / survey_stats['total_responses']) * 100
            )

        satisfaction_dist = list(
            surveys.values('overall_satisfaction')
            .annotate(count=Count('id'))
            .order_by('overall_satisfaction')
        )

        recent_feedback_text = surveys.exclude(
            liked_most__isnull=True
        ).exclude(
            liked_most=''
        ).order_by('-created_at')[:5].values('liked_most', 'improvement_suggestion', 'created_at')

        events = AnalyticsEvent.objects.all()
        event_counts = dict(
            events.values('event_type')
            .annotate(count=Count('id'))
            .values_list('event_type', 'count')
        )

        seven_days_ago = timezone.now() - timedelta(days=7)
        daily_activity_qs = (
            events.filter(created_at__gte=seven_days_ago)
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')
        )
        daily_activity = [
            {'date': entry['date'].isoformat(), 'count': entry['count']}
            for entry in daily_activity_qs
        ]

        feedback = RecommendationFeedback.objects.all()
        feedback_stats = {
            'total': feedback.count(),
            'likes': feedback.filter(score=True).count(),
            'dislikes': feedback.filter(score=False).count(),
        }
        if feedback_stats['total'] > 0:
            feedback_stats['like_rate'] = round(
                (feedback_stats['likes'] / feedback_stats['total']) * 100
            )
        else:
            feedback_stats['like_rate'] = 0

        unique_sessions = events.values('session_key').distinct().count()

        return render(request, self.template_name, {
            'survey_stats': survey_stats,
            'satisfaction_dist': satisfaction_dist,
            'recent_feedback_text': list(recent_feedback_text),
            'event_counts': event_counts,
            'daily_activity': json.dumps(daily_activity),
            'feedback_stats': feedback_stats,
            'unique_sessions': unique_sessions,
        })


class SpotifyExportInitView(View):
    """Starts the Spotify OAuth flow to export a playlist."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Redirect to Spotify auth page."""
        track_ids = request.GET.getlist('track_ids')
        if not track_ids:
            track_ids = request.session.get('last_recommendations', [])

        if not track_ids:
            return JsonResponse({
                'error': 'No tracks to export. Generate recommendations first.'
            }, status=400)

        request.session['spotify_export_tracks'] = track_ids[:50]

        redirect_uri = request.build_absolute_uri('/spotify/callback/')

        try:
            client = SpotifyUserClient()
            auth_url, state = client.get_authorization_url(redirect_uri)

            request.session['spotify_oauth_state'] = state
            request.session.modified = True

            return redirect(auth_url)

        except SpotifyOAuthError as e:
            return render(request, 'catalog/spotify_export_error.html', {
                'error': str(e),
                'message': 'Spotify credentials are not configured. Please contact the administrator.'
            })


class SpotifyCallbackView(View):
    """Handles Spotify OAuth callback and creates the playlist."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Exchange auth code for token, then create the playlist."""
        error = request.GET.get('error')
        if error:
            return render(request, 'catalog/spotify_export_error.html', {
                'error': error,
                'message': 'Authorization was denied or cancelled.'
            })

        state = request.GET.get('state')
        stored_state = request.session.get('spotify_oauth_state')

        if not state or state != stored_state:
            return render(request, 'catalog/spotify_export_error.html', {
                'error': 'Invalid state parameter',
                'message': 'Security validation failed. Please try again.'
            })

        code = request.GET.get('code')
        if not code:
            return render(request, 'catalog/spotify_export_error.html', {
                'error': 'No authorization code',
                'message': 'Authorization failed. Please try again.'
            })

        redirect_uri = request.build_absolute_uri('/spotify/callback/')

        try:
            client = SpotifyUserClient()
            token_data = client.exchange_code_for_token(code, redirect_uri)

            track_ids = request.session.get('spotify_export_tracks', [])

            if not track_ids:
                return render(request, 'catalog/spotify_export_error.html', {
                    'error': 'No tracks found',
                    'message': 'Session expired. Please try exporting again.'
                })

            tracks = Track.objects.filter(id__in=track_ids)
            spotify_track_ids = []

            for track in tracks:
                if track.id and track.id.startswith('spotify:'):
                    spotify_track_ids.append(track.id.split(':')[-1])
                else:
                    spotify_track_ids.append(track.id)

            result = client.export_recommendations(
                track_ids=spotify_track_ids,
                playlist_name=None,  # Auto-generate name
                description="Personalized recommendations from NextTrack - AI-powered music discovery."
            )

            if 'spotify_oauth_state' in request.session:
                del request.session['spotify_oauth_state']
            if 'spotify_export_tracks' in request.session:
                del request.session['spotify_export_tracks']
            request.session.modified = True

            if result['success']:
                return render(request, 'catalog/spotify_export_success.html', {
                    'playlist_url': result['playlist_url'],
                    'playlist_name': result['playlist_name'],
                    'tracks_added': result['tracks_added'],
                })
            else:
                return render(request, 'catalog/spotify_export_error.html', {
                    'error': result.get('error', 'Unknown error'),
                    'message': 'Failed to create playlist. Please try again.'
                })

        except SpotifyOAuthError as e:
            return render(request, 'catalog/spotify_export_error.html', {
                'error': str(e),
                'message': 'Authentication failed. Please try again.'
            })



@require_POST
def spotify_export_ajax(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint that returns the Spotify auth URL for export."""

    try:
        if request.content_type and 'application/json' in request.content_type:
            data = json.loads(request.body)
            track_ids = data.get('track_ids', [])
        else:
            track_ids = request.POST.getlist('track_ids')

        if not track_ids:
            return JsonResponse({
                'error': 'No tracks provided'
            }, status=400)

        request.session['spotify_export_tracks'] = track_ids[:50]

        redirect_uri = request.build_absolute_uri('/spotify/callback/')
        client = SpotifyUserClient()

        if not client.is_configured:
            return JsonResponse({
                'error': 'Spotify integration not configured',
                'configured': False
            }, status=503)

        auth_url, state = client.get_authorization_url(redirect_uri)

        request.session['spotify_oauth_state'] = state
        request.session.modified = True

        return JsonResponse({
            'auth_url': auth_url,
            'configured': True
        })

    except SpotifyOAuthError as e:
        return JsonResponse({
            'error': str(e),
            'configured': False
        }, status=500)
    except Exception as e:
        return JsonResponse({
            'error': str(e)
        }, status=500)


# =============================================================================
# SHAREABLE SNAPSHOTS
# =============================================================================

@require_POST
def create_snapshot(request: HttpRequest) -> JsonResponse:
    """Create a shareable snapshot of the current playlist and preferences."""
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        data = {}

    track_ids = data.get('track_ids') or request.session.get('playlist', [])
    preferences = data.get('preferences')

    if not track_ids:
        return JsonResponse({'error': 'No tracks to share'}, status=400)

    from datetime import timedelta
    snapshot = SharedPlaylist.objects.create(
        track_ids=track_ids,
        preferences=preferences,
        expires_at=timezone.now() + timedelta(days=30),
    )

    return JsonResponse({
        'status': 'created',
        'share_url': f'/shared/{snapshot.id}/',
        'token': str(snapshot.id),
        'expires_in_days': 30,
    })


class SharedPlaylistView(View):
    """View a shared playlist snapshot."""
    template_name = 'catalog/results.html'

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        snapshot = get_object_or_404(SharedPlaylist, id=token)

        if snapshot.expires_at and snapshot.expires_at < timezone.now():
            return render(request, 'catalog/home.html', {
                'error_message': 'This shared playlist has expired.'
            })

        snapshot.view_count += 1
        snapshot.save(update_fields=['view_count'])

        tracks = list(
            Track.objects.filter(id__in=snapshot.track_ids)
            .select_related('artist')
            .prefetch_related('genres')
        )

        result = get_enhanced_recommendations(
            track_ids=snapshot.track_ids,
            preferences=snapshot.preferences or {},
            limit=10,
        )

        context = {
            'playlist_tracks': tracks,
            'recommendations': result.get('recommendations', []),
            'centroid': result.get('centroid', {}),
            'explanations': result.get('explanations', {}),
            'is_shared': True,
            'share_token': str(snapshot.id),
            'share_view_count': snapshot.view_count,
        }
        return render(request, self.template_name, context)


@require_POST
def copy_shared_playlist(request: HttpRequest, token: str) -> JsonResponse:
    """Copy a shared playlist into the user's session."""
    snapshot = get_object_or_404(SharedPlaylist, id=token)

    if not request.session.session_key:
        request.session.create()

    request.session['playlist'] = snapshot.track_ids
    request.session.modified = True

    return JsonResponse({
        'status': 'copied',
        'track_count': len(snapshot.track_ids),
    })


# =============================================================================
# TRACK COMPARISON
# =============================================================================

class TrackCompareView(View):
    """Side-by-side comparison of two tracks' audio features."""
    template_name = 'catalog/compare.html'

    def get(self, request: HttpRequest, track_a: str = None, track_b: str = None) -> HttpResponse:
        from catalog.services import get_feature_vector, euclidean_distance

        context = {'comparison': None}

        if track_a and track_b:
            try:
                ta = Track.objects.select_related('artist').prefetch_related('genres').get(pk=track_a)
                tb = Track.objects.select_related('artist').prefetch_related('genres').get(pk=track_b)
            except Track.DoesNotExist:
                context['error'] = 'One or both tracks not found.'
                return render(request, self.template_name, context)

            vec_a = get_feature_vector(ta)
            vec_b = get_feature_vector(tb)
            distance = euclidean_distance(vec_a, vec_b)
            similarity = max(0, round((1 - distance) * 100, 1))

            features = ['valence', 'energy', 'danceability', 'acousticness', 'tempo']
            diffs = {}
            for feat in features:
                val_a = getattr(ta, feat)
                val_b = getattr(tb, feat)
                if feat == 'tempo':
                    val_a /= 200.0
                    val_b /= 200.0
                diffs[feat] = round((val_a - val_b) * 100, 1)

            genres_a = set(ta.genres.values_list('name', flat=True))
            genres_b = set(tb.genres.values_list('name', flat=True))

            context['comparison'] = {
                'track_a': ta,
                'track_b': tb,
                'similarity': similarity,
                'distance': round(distance, 4),
                'feature_diffs': diffs,
                'shared_genres': list(genres_a & genres_b),
                'unique_a': list(genres_a - genres_b),
                'unique_b': list(genres_b - genres_a),
                'features_a': [float(getattr(ta, f)) for f in features],
                'features_b': [float(getattr(tb, f)) for f in features],
            }

        return render(request, self.template_name, context)


class MoodJourneyView(View):
    """Mood transition playlist generator."""
    template_name = 'catalog/journey.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, {})

    def post(self, request: HttpRequest) -> HttpResponse:
        from catalog.services import generate_mood_journey

        start = {
            'valence': float(request.POST.get('start_valence', 0.3)),
            'energy': float(request.POST.get('start_energy', 0.3)),
            'danceability': float(request.POST.get('start_danceability', 0.4)),
            'acousticness': float(request.POST.get('start_acousticness', 0.6)),
            'tempo': float(request.POST.get('start_tempo', 90)),
        }
        end = {
            'valence': float(request.POST.get('end_valence', 0.8)),
            'energy': float(request.POST.get('end_energy', 0.8)),
            'danceability': float(request.POST.get('end_danceability', 0.7)),
            'acousticness': float(request.POST.get('end_acousticness', 0.2)),
            'tempo': float(request.POST.get('end_tempo', 140)),
        }
        steps = min(max(3, int(request.POST.get('steps', 8))), 15)

        journey = generate_mood_journey(start, end, steps=steps)

        return render(request, self.template_name, {
            'journey': journey,
            'start': start,
            'end': end,
            'steps': steps,
        })


class GenreLineageView(View):
    """Genre lineage map visualization (data loaded via AJAX)."""
    template_name = 'catalog/lineage.html'

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name)


class ScatterPlotView(View):
    """Interactive scatter plot explorer for audio features."""

    def get(self, request):
        genres = list(Genre.objects.values_list('name', flat=True).order_by('name')[:50])
        return render(request, 'catalog/scatter.html', {'genres': genres})
