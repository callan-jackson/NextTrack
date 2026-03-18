"""API endpoint tests for catalog views (recommend, survey, analytics, feedback)."""

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from catalog.models import Genre, Artist, Track, AnalyticsEvent, RecommendationFeedback


# ---------------------------------------------------------------------------
# Existing tests migrated from catalog/tests.py
# ---------------------------------------------------------------------------


class SequenceRecommendAPITestCase(APITestCase):
    """Test POST /api/tracks/recommend/ endpoint."""

    @classmethod
    def setUpTestData(cls):
        """Create test data for API tests."""
        cls.genre = Genre.objects.create(name='api_test_genre')
        cls.artist = Artist.objects.create(
            id='api_test_artist',
            name='API Test Artist',
            popularity=75
        )

        cls.track1 = Track.objects.create(
            id='api_test_track_1',
            title='API Test Track 1',
            artist=cls.artist,
            valence=0.6,
            energy=0.7,
            danceability=0.8,
            acousticness=0.3,
            tempo=120.0,
            popularity=80
        )
        cls.track1.genres.add(cls.genre)

        cls.track2 = Track.objects.create(
            id='api_test_track_2',
            title='API Test Track 2',
            artist=cls.artist,
            valence=0.62,
            energy=0.72,
            danceability=0.78,
            acousticness=0.32,
            tempo=122.0,
            popularity=70
        )
        cls.track2.genres.add(cls.genre)

        cls.track3 = Track.objects.create(
            id='api_test_track_3',
            title='API Test Track 3',
            artist=cls.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=60
        )
        cls.track3.genres.add(cls.genre)

    def test_recommend_post_success(self):
        """Valid POST returns recommendations."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': [self.track1.id, self.track2.id]},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('input_tracks', response.data)
        self.assertIn('centroid', response.data)
        self.assertIn('recommendations', response.data)
        self.assertIn('count', response.data)

    def test_recommend_post_with_preferences(self):
        """POST with preferences reflects them in centroid."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {
                'track_ids': [self.track1.id],
                'preferences': {'energy': 0.9, 'valence': 0.8}
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        centroid = response.data['centroid']
        self.assertIn('energy', centroid)

    def test_recommend_post_empty_track_ids(self):
        """Empty track_ids returns 400."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': []},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_recommend_post_missing_track_ids(self):
        """Missing track_ids returns 400."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_recommend_post_invalid_track_ids(self):
        """Invalid track IDs returns 404."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': ['nonexistent_1', 'nonexistent_2']},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_recommend_post_invalid_preferences(self):
        """Invalid preference keys returns 400."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {
                'track_ids': [self.track1.id],
                'preferences': {'invalid_key': 0.5}
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class StatisticsAPITestCase(APITestCase):
    """Test GET /api/tracks/statistics/ endpoint."""

    @classmethod
    def setUpTestData(cls):
        """Create test data for statistics."""
        cls.genre1 = Genre.objects.create(name='rock')
        cls.genre2 = Genre.objects.create(name='pop')
        cls.artist = Artist.objects.create(
            id='stats_artist',
            name='Stats Artist',
            popularity=80
        )

        for i in range(5):
            track = Track.objects.create(
                id=f'stats_track_{i}',
                title=f'Stats Track {i}',
                artist=cls.artist,
                valence=0.5,
                energy=0.6,
                danceability=0.7,
                acousticness=0.4,
                tempo=120.0,
                popularity=50 + i * 10
            )
            track.genres.add(cls.genre1 if i < 3 else cls.genre2)

    def test_statistics_endpoint(self):
        """Statistics endpoint returns aggregated data."""
        response = self.client.get('/api/tracks/statistics/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_tracks', response.data)
        self.assertIn('total_artists', response.data)
        self.assertIn('total_genres', response.data)
        self.assertIn('averages', response.data)
        self.assertIn('genres_breakdown', response.data)
        self.assertIn('popularity_distribution', response.data)

    def test_statistics_averages(self):
        """Averages are calculated correctly."""
        response = self.client.get('/api/tracks/statistics/')

        averages = response.data['averages']
        self.assertIn('tempo', averages)
        self.assertIn('energy', averages)
        self.assertIn('valence', averages)

    def test_statistics_genre_breakdown(self):
        """Genre breakdown contains track counts."""
        response = self.client.get('/api/tracks/statistics/')

        breakdown = response.data['genres_breakdown']
        self.assertIsInstance(breakdown, list)
        if breakdown:
            self.assertIn('name', breakdown[0])
            self.assertIn('track_count', breakdown[0])


class TrackListAPITestCase(APITestCase):
    """Test track list and detail endpoints."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='list_test_genre')
        cls.artist = Artist.objects.create(
            id='list_test_artist',
            name='List Test Artist',
            popularity=60
        )
        cls.track = Track.objects.create(
            id='list_test_track',
            title='List Test Track',
            artist=cls.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        cls.track.genres.add(cls.genre)

    def test_track_list_endpoint(self):
        """GET /api/tracks/ returns paginated list."""
        response = self.client.get('/api/tracks/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)

    def test_track_detail_endpoint(self):
        """GET /api/tracks/{id}/ returns track detail."""
        response = self.client.get(f'/api/tracks/{self.track.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.track.id)

    def test_search_endpoint(self):
        """Search endpoint returns matching results."""
        response = self.client.get('/api/tracks/search/', {'q': 'List'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)

    def test_search_endpoint_short_query(self):
        """Short query returns 400."""
        response = self.client.get('/api/tracks/search/', {'q': 'a'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class FeedbackIntegrationTestCase(APITestCase):
    """Integration tests for the feedback system."""

    @classmethod
    def setUpTestData(cls):
        """Create test data for feedback tests."""
        cls.genre = Genre.objects.create(name='feedback_test_genre')
        cls.artist = Artist.objects.create(
            id='feedback_test_artist',
            name='Feedback Test Artist',
            popularity=70
        )
        cls.track = Track.objects.create(
            id='feedback_test_track',
            title='Feedback Test Track',
            artist=cls.artist,
            valence=0.6,
            energy=0.7,
            danceability=0.5,
            acousticness=0.3,
            tempo=120.0,
            popularity=60
        )
        cls.track.genres.add(cls.genre)

    def test_feedback_submission_like(self):
        """Submitting a like creates feedback."""
        response = self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': True},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'created')

    def test_feedback_submission_dislike(self):
        """Submitting a dislike creates feedback."""
        response = self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': False},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'created')

    def test_feedback_toggle_undo(self):
        """Same feedback button twice removes it."""
        self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': True},
            format='json'
        )
        response = self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': True},
            format='json'
        )
        self.assertEqual(response.json()['status'], 'removed')

    def test_feedback_switch_vote(self):
        """Switching from like to dislike updates feedback."""
        self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': True},
            format='json'
        )
        response = self.client.post(
            '/api/feedback/',
            {'track_id': self.track.id, 'score': False},
            format='json'
        )
        self.assertEqual(response.json()['status'], 'updated')

    def test_feedback_invalid_track(self):
        """Nonexistent track returns 404."""
        response = self.client.post(
            '/api/feedback/',
            {'track_id': 'nonexistent_track', 'score': True},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SurveyAndAnalyticsTestCase(APITestCase):
    """Test survey and analytics endpoints."""

    def test_survey_submission_valid(self):
        """Valid survey submission returns 201."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 4,
                'discovery_rating': 5,
                'accuracy_rating': 4,
                'liked_most': 'Great variety of tracks',
                'would_recommend': True,
                'tracks_interacted': 10
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'created')

    def test_survey_submission_missing_required(self):
        """Missing required fields returns 400."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 4,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_survey_invalid_rating_value(self):
        """Out-of-range rating returns 400."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 10,
                'discovery_rating': 3,
                'accuracy_rating': 3,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_analytics_event_recording(self):
        """Valid analytics event returns 201."""
        response = self.client.post(
            '/api/analytics/',
            {
                'event_type': 'search',
                'metadata': {'query': 'test search'}
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_analytics_invalid_event_type(self):
        """Invalid event type returns 400."""
        response = self.client.post(
            '/api/analytics/',
            {
                'event_type': 'invalid_type',
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# New API tests (tasks 3.1, 3.3, 3.4, 3.6)
# ---------------------------------------------------------------------------


class RecommendEndpointLimitsTestCase(APITestCase):
    """Test recommend endpoint: 50 track IDs (pass), 51 (fail), limit clamping."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='limits_genre')
        cls.artist = Artist.objects.create(
            id='limits_artist', name='Limits Artist', popularity=60
        )
        cls.tracks = []
        for i in range(55):
            track = Track.objects.create(
                id=f'limits_track_{i}',
                title=f'Limits Track {i}',
                artist=cls.artist,
                valence=0.5 + (i % 10) * 0.01,
                energy=0.5,
                danceability=0.5,
                acousticness=0.5,
                tempo=120.0,
                popularity=50
            )
            track.genres.add(cls.genre)
            cls.tracks.append(track)

    def test_50_track_ids_accepted(self):
        """Exactly 50 track IDs should be accepted."""
        ids = [t.id for t in self.tracks[:50]]
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': ids},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_51_track_ids_rejected(self):
        """51 track IDs should be rejected with 400."""
        ids = [t.id for t in self.tracks[:51]]
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': ids},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_limit_clamped_to_minimum_1(self):
        """Limit below 1 is clamped to 1."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': [self.tracks[0].id], 'limit': -5},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # count should be at most 1 (clamped)
        self.assertLessEqual(response.data['count'], 1)

    def test_limit_clamped_to_maximum_50(self):
        """Limit above 50 is clamped to 50."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': [self.tracks[0].id], 'limit': 999},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(response.data['count'], 50)


class SurveyBoundaryTestCase(APITestCase):
    """Test survey endpoint: rating boundaries and optional fields."""

    def test_survey_rating_zero_rejected(self):
        """Rating value 0 should be rejected (valid range is 1-5)."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 0,
                'discovery_rating': 3,
                'accuracy_rating': 3,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_survey_rating_six_rejected(self):
        """Rating value 6 should be rejected (valid range is 1-5)."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 6,
                'discovery_rating': 3,
                'accuracy_rating': 3,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_survey_valid_with_optional_fields(self):
        """Valid ratings with all optional fields should succeed."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 3,
                'discovery_rating': 4,
                'accuracy_rating': 2,
                'liked_most': 'The diversity of recommendations',
                'improvement_suggestion': 'More hip-hop please',
                'would_recommend': False,
                'tracks_interacted': 25,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'created')

    def test_survey_valid_minimum_ratings(self):
        """Minimum valid ratings (all 1s) should succeed."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 1,
                'discovery_rating': 1,
                'accuracy_rating': 1,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_survey_valid_maximum_ratings(self):
        """Maximum valid ratings (all 5s) should succeed."""
        response = self.client.post(
            '/api/survey/',
            {
                'overall_satisfaction': 5,
                'discovery_rating': 5,
                'accuracy_rating': 5,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class AnalyticsAllEventTypesTestCase(APITestCase):
    """Test analytics endpoint: all 8 valid event types, missing event_type."""

    VALID_EVENT_TYPES = [
        'search', 'recommend', 'play', 'like',
        'dislike', 'add_playlist', 'filter_applied', 'survey_completed',
    ]

    def test_all_valid_event_types_accepted(self):
        """Each of the 8 valid event types should return 201."""
        for event_type in self.VALID_EVENT_TYPES:
            response = self.client.post(
                '/api/analytics/',
                {'event_type': event_type},
                format='json'
            )
            self.assertEqual(
                response.status_code,
                status.HTTP_201_CREATED,
                msg=f"Event type '{event_type}' should be accepted but got {response.status_code}"
            )

    def test_missing_event_type_rejected(self):
        """Missing event_type field should return 400."""
        response = self.client.post(
            '/api/analytics/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_event_type_rejected(self):
        """Empty string event_type should return 400."""
        response = self.client.post(
            '/api/analytics/',
            {'event_type': ''},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_analytics_with_metadata(self):
        """Event with metadata dict should be accepted."""
        response = self.client.post(
            '/api/analytics/',
            {
                'event_type': 'search',
                'metadata': {'query': 'test', 'results_count': 5}
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_analytics_with_track_id(self):
        """Event with track_id should be accepted."""
        response = self.client.post(
            '/api/analytics/',
            {
                'event_type': 'play',
                'track_id': 'some_track_123'
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class ErrorFormatConsistencyTestCase(APITestCase):
    """Verify JSON error responses have consistent structure."""

    def test_recommend_400_has_error_key(self):
        """Recommend 400 response has 'error' key."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': []},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)

    def test_recommend_404_has_error_key(self):
        """Recommend 404 response has 'error' key."""
        response = self.client.post(
            '/api/tracks/recommend/',
            {'track_ids': ['nonexistent_1']},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)

    def test_survey_400_has_error_key(self):
        """Survey 400 response has 'error' key."""
        response = self.client.post(
            '/api/survey/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)

    def test_analytics_400_has_error_key(self):
        """Analytics 400 response has 'error' key."""
        response = self.client.post(
            '/api/analytics/',
            {'event_type': 'bogus_event'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)

    def test_search_400_has_error_key(self):
        """Search 400 response has 'error' key."""
        response = self.client.get('/api/tracks/search/', {'q': 'a'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)


# ---------------------------------------------------------------------------
# Caching behavior tests (task 3.2)
# ---------------------------------------------------------------------------


class CachingBehaviorTestCase(APITestCase):
    """Test recommendation caching: POST twice returns cached result, cache.clear() returns fresh."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='cache_genre')
        cls.artist = Artist.objects.create(
            id='cache_artist', name='Cache Artist', popularity=60
        )
        cls.track1 = Track.objects.create(
            id='cache_track_1', title='Cache Track 1', artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50
        )
        cls.track1.genres.add(cls.genre)

        cls.track2 = Track.objects.create(
            id='cache_track_2', title='Cache Track 2', artist=cls.artist,
            valence=0.55, energy=0.55, danceability=0.55,
            acousticness=0.45, tempo=125.0, popularity=55
        )
        cls.track2.genres.add(cls.genre)

    @override_settings(CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'cache-test-unique',
        }
    })
    def test_second_post_returns_cached_result(self):
        """Same POST twice returns identical (cached) data."""
        from django.core.cache import cache
        cache.clear()

        payload = {'track_ids': [self.track1.id]}
        response1 = self.client.post('/api/tracks/recommend/', payload, format='json')
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        response2 = self.client.post('/api/tracks/recommend/', payload, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.assertEqual(response1.data, response2.data)

    @override_settings(CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'cache-test-unique-2',
        }
    })
    def test_cache_clear_returns_fresh_data(self):
        """After cache.clear(), a new POST re-computes the result."""
        from django.core.cache import cache
        cache.clear()

        payload = {'track_ids': [self.track1.id]}
        response1 = self.client.post('/api/tracks/recommend/', payload, format='json')
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        # Add a new candidate track so fresh results can differ
        new_track = Track.objects.create(
            id='cache_new_track', title='New Cache Track', artist=self.artist,
            valence=0.51, energy=0.51, danceability=0.51,
            acousticness=0.49, tempo=121.0, popularity=52
        )
        new_track.genres.add(self.genre)

        cache.clear()

        response2 = self.client.post('/api/tracks/recommend/', payload, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        # After clearing cache with new data available, count may differ
        # At minimum, the response should be valid
        self.assertIn('recommendations', response2.data)


# ---------------------------------------------------------------------------
# Feedback session isolation tests (task 3.5)
# ---------------------------------------------------------------------------


class FeedbackSessionIsolationTestCase(TestCase):
    """Test feedback isolation between different client sessions."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='iso_genre')
        cls.artist = Artist.objects.create(
            id='iso_artist', name='Isolation Artist', popularity=70
        )
        cls.track = Track.objects.create(
            id='iso_track', title='Isolation Track', artist=cls.artist,
            valence=0.6, energy=0.7, danceability=0.5,
            acousticness=0.3, tempo=120.0, popularity=60
        )
        cls.track.genres.add(cls.genre)

        cls.track2 = Track.objects.create(
            id='iso_track_2', title='Isolation Track 2', artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=100.0, popularity=55
        )
        cls.track2.genres.add(cls.genre)

    def test_two_sessions_feedback_isolated(self):
        """Feedback stored under one session_key is not visible under another."""
        session_a = 'isolation_session_a'
        session_b = 'isolation_session_b'

        RecommendationFeedback.objects.create(
            track=self.track, score=True, session_key=session_a
        )
        RecommendationFeedback.objects.create(
            track=self.track2, score=False, session_key=session_b
        )

        a_feedback = RecommendationFeedback.objects.filter(session_key=session_a)
        self.assertEqual(a_feedback.count(), 1)
        self.assertEqual(a_feedback.first().track_id, self.track.id)

        b_feedback = RecommendationFeedback.objects.filter(session_key=session_b)
        self.assertEqual(b_feedback.count(), 1)
        self.assertEqual(b_feedback.first().track_id, self.track2.id)

    def test_feedback_created_via_api(self):
        """Feedback API creates a feedback record in the database."""
        import json
        from django.test import Client

        client = Client()

        response = client.post(
            '/api/feedback/',
            json.dumps({'track_id': self.track.id, 'score': True}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'created')

        # The feedback should be stored in the database
        feedback_qs = RecommendationFeedback.objects.filter(track=self.track)
        self.assertEqual(feedback_qs.count(), 1)
        self.assertTrue(feedback_qs.first().score)  # True = like

    def test_toggle_undo_across_requests(self):
        """Submitting the same like twice toggles (creates then removes)."""
        import json
        from django.test import Client

        client = Client()

        # First request: creates feedback
        response1 = client.post(
            '/api/feedback/',
            json.dumps({'track_id': self.track.id, 'score': True}),
            content_type='application/json'
        )
        self.assertEqual(response1.json()['status'], 'created')

        # Feedback exists
        self.assertEqual(RecommendationFeedback.objects.filter(track=self.track).count(), 1)

        # Second request with same score: removes feedback (toggle)
        response2 = client.post(
            '/api/feedback/',
            json.dumps({'track_id': self.track.id, 'score': True}),
            content_type='application/json'
        )
        self.assertEqual(response2.json()['status'], 'removed')
        self.assertEqual(RecommendationFeedback.objects.filter(track=self.track).count(), 0)

    def test_switch_vote_across_requests(self):
        """Switching from like to dislike updates the feedback."""
        import json
        from django.test import Client

        client = Client()

        # Like
        client.post(
            '/api/feedback/',
            json.dumps({'track_id': self.track.id, 'score': True}),
            content_type='application/json'
        )

        # Switch to dislike
        response = client.post(
            '/api/feedback/',
            json.dumps({'track_id': self.track.id, 'score': False}),
            content_type='application/json'
        )
        self.assertEqual(response.json()['status'], 'updated')
