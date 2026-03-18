"""Web view integration tests for the Django frontend pages (task 2.5)."""

import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from catalog.models import Genre, Artist, Track, RecommendationFeedback


# Common overrides: disable whitenoise manifest, use locmem cache,
# switch to DB-backed sessions so _force_session helper works.
WEB_TEST_SETTINGS = {
    'STATICFILES_STORAGE': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    'SESSION_ENGINE': 'django.contrib.sessions.backends.db',
    'CACHES': {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'web-view-tests',
        }
    },
}


def _force_session(client, data):
    """Persist session data into the Django test client (DB backend)."""
    from importlib import import_module
    from django.conf import settings

    engine = import_module(settings.SESSION_ENGINE)
    store = engine.SessionStore()
    for key, value in data.items():
        store[key] = value
    store.create()
    client.cookies[settings.SESSION_COOKIE_NAME] = store.session_key


@override_settings(**WEB_TEST_SETTINGS)
class HomeViewTestCase(TestCase):
    """Test HomeView (GET /)."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="home_genre")
        cls.artist = Artist.objects.create(
            id="home_artist", name="Home Artist", popularity=60
        )
        cls.track = Track.objects.create(
            id="home_track", title="Home Track",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
        )
        cls.track.genres.add(cls.genre)

    def test_home_get_returns_200(self):
        """GET / returns 200 and uses the home template."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'catalog/home.html')

    def test_home_context_keys(self):
        """Home page context contains expected keys."""
        response = self.client.get('/')
        self.assertIn('query', response.context)
        self.assertIn('results', response.context)
        self.assertIn('playlist_count', response.context)

    def test_home_empty_query(self):
        """GET / with no query returns empty results."""
        response = self.client.get('/')
        self.assertEqual(response.context['results'], [])
        self.assertEqual(response.context['query'], '')

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_home_search_returns_results(self, mock_spotify):
        """GET /?q=Home returns matching tracks in context."""
        response = self.client.get('/', {'q': 'Home'})
        self.assertEqual(response.status_code, 200)
        results = response.context['results']
        result_ids = [t.id for t in results]
        self.assertIn('home_track', result_ids)

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_home_search_short_query_returns_empty(self, mock_spotify):
        """GET /?q=H (single char) returns empty results."""
        response = self.client.get('/', {'q': 'H'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['results'], [])


@override_settings(**WEB_TEST_SETTINGS)
class PlaylistBuilderViewTestCase(TestCase):
    """Test PlaylistBuilderView (GET /builder/, POST /builder/)."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="builder_genre")
        cls.artist = Artist.objects.create(
            id="builder_artist", name="Builder Artist", popularity=60
        )
        cls.track1 = Track.objects.create(
            id="builder_track_1", title="Builder Track 1",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
        )
        cls.track1.genres.add(cls.genre)

        cls.track2 = Track.objects.create(
            id="builder_track_2", title="Builder Track 2",
            artist=cls.artist,
            valence=0.6, energy=0.6, danceability=0.6,
            acousticness=0.4, tempo=130.0, popularity=55,
        )
        cls.track2.genres.add(cls.genre)

    def test_builder_get_empty_playlist(self):
        """GET /builder/ with empty session returns empty playlist."""
        response = self.client.get('/builder/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'catalog/builder.html')
        self.assertEqual(response.context['playlist_count'], 0)
        self.assertEqual(response.context['playlist_tracks'], [])

    def test_builder_get_with_session_tracks(self):
        """GET /builder/ with tracks in session returns those tracks."""
        _force_session(self.client, {
            'playlist': ['builder_track_1', 'builder_track_2'],
        })

        response = self.client.get('/builder/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['playlist_count'], 2)
        track_ids = [t.id for t in response.context['playlist_tracks']]
        self.assertIn('builder_track_1', track_ids)
        self.assertIn('builder_track_2', track_ids)

    def test_builder_post_add_track(self):
        """POST /builder/ with action=add adds track to session."""
        response = self.client.post('/builder/', {
            'action': 'add',
            'track_id': 'builder_track_1',
        })
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertIn('builder_track_1', session.get('playlist', []))

    def test_builder_post_remove_track(self):
        """POST /builder/ with action=remove removes track from session."""
        # First add the tracks using POST
        self.client.post('/builder/', {
            'action': 'add', 'track_id': 'builder_track_1',
        })
        self.client.post('/builder/', {
            'action': 'add', 'track_id': 'builder_track_2',
        })

        # Now remove one
        response = self.client.post('/builder/', {
            'action': 'remove',
            'track_id': 'builder_track_1',
        })
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertNotIn('builder_track_1', session['playlist'])
        self.assertIn('builder_track_2', session['playlist'])

    def test_builder_post_clear(self):
        """POST /builder/ with action=clear empties the playlist."""
        self.client.post('/builder/', {
            'action': 'add', 'track_id': 'builder_track_1',
        })

        response = self.client.post('/builder/', {
            'action': 'clear',
        })
        self.assertEqual(response.status_code, 302)

        session = self.client.session
        self.assertEqual(session['playlist'], [])


@override_settings(**WEB_TEST_SETTINGS)
class RecommendationsViewTestCase(TestCase):
    """Test RecommendationsView (GET /results/)."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="results_genre")
        cls.artist = Artist.objects.create(
            id="results_artist", name="Results Artist",
            popularity=70, origin_country="US", formed_year=2000,
        )
        cls.input_track = Track.objects.create(
            id="results_input", title="Results Input",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=70,
        )
        cls.input_track.genres.add(cls.genre)

        # Create candidate tracks
        for i in range(5):
            t = Track.objects.create(
                id=f"results_cand_{i}", title=f"Results Candidate {i}",
                artist=cls.artist,
                valence=0.5 + i * 0.02, energy=0.5 + i * 0.02,
                danceability=0.5, acousticness=0.5,
                tempo=120.0 + i, popularity=60 + i,
            )
            t.genres.add(cls.genre)

    def _setup_playlist_session(self):
        """Populate session with the input track in the playlist."""
        _force_session(self.client, {'playlist': ['results_input']})

    @patch('catalog.views_web.get_live_external_service')
    def test_results_empty_playlist_shows_error(self, mock_ext):
        """GET /results/ without playlist shows error message."""
        mock_ext.return_value = MagicMock()
        response = self.client.get('/results/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('error', response.context)
        self.assertIn('empty', response.context['error'].lower())

    @patch('catalog.views_web.get_live_external_service')
    def test_results_with_playlist_returns_recommendations(self, mock_ext):
        """GET /results/ with playlist in session returns recommendations."""
        mock_service = MagicMock()
        mock_service.batch_get_artist_info.return_value = {}
        mock_ext.return_value = mock_service

        self._setup_playlist_session()

        response = self.client.get('/results/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('recommendations', response.context)
        self.assertIn('input_tracks', response.context)
        self.assertIn('centroid', response.context)
        self.assertGreater(len(response.context['recommendations']), 0)

    @patch('catalog.views_web.get_live_external_service')
    def test_results_context_contains_chart_data(self, mock_ext):
        """Context includes JSON data for the radar chart."""
        mock_service = MagicMock()
        mock_service.batch_get_artist_info.return_value = {}
        mock_ext.return_value = mock_service

        self._setup_playlist_session()

        response = self.client.get('/results/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('input_vibe_json', response.context)
        self.assertIn('result_vibe_json', response.context)

        # Verify the JSON is valid
        input_vibe = json.loads(response.context['input_vibe_json'])
        self.assertIn('energy', input_vibe)
        self.assertIn('valence', input_vibe)

    @patch('catalog.views_web.get_live_external_service')
    def test_results_with_filters(self, mock_ext):
        """GET /results/?country=US filters by country."""
        mock_service = MagicMock()
        mock_service.batch_get_artist_info.return_value = {}
        mock_ext.return_value = mock_service

        self._setup_playlist_session()

        response = self.client.get('/results/', {'country': 'US'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('recommendations', response.context)

    @patch('catalog.views_web.get_live_external_service')
    def test_results_template_used(self, mock_ext):
        """Results page uses the correct template."""
        mock_service = MagicMock()
        mock_service.batch_get_artist_info.return_value = {}
        mock_ext.return_value = mock_service

        self._setup_playlist_session()

        response = self.client.get('/results/')
        self.assertTemplateUsed(response, 'catalog/results.html')


@override_settings(**WEB_TEST_SETTINGS)
class AddToPlaylistAjaxTestCase(TestCase):
    """Test add_to_playlist_ajax (POST /ajax/add-track/)."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="ajax_genre")
        cls.artist = Artist.objects.create(
            id="ajax_artist", name="Ajax Artist", popularity=60
        )
        cls.track = Track.objects.create(
            id="ajax_track", title="Ajax Track",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
        )
        cls.track.genres.add(cls.genre)

    def test_add_track_success(self):
        """POST /ajax/add-track/ with valid track_id adds to playlist."""
        response = self.client.post(
            '/ajax/add-track/',
            json.dumps({'track_id': 'ajax_track'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'added')
        self.assertEqual(data['count'], 1)

    def test_add_track_duplicate(self):
        """Adding the same track twice returns 'duplicate'."""
        # First add
        self.client.post(
            '/ajax/add-track/',
            json.dumps({'track_id': 'ajax_track'}),
            content_type='application/json',
        )
        # Second add should be duplicate
        response = self.client.post(
            '/ajax/add-track/',
            json.dumps({'track_id': 'ajax_track'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'duplicate')

    def test_add_track_nonexistent(self):
        """Adding a nonexistent track returns 404."""
        response = self.client.post(
            '/ajax/add-track/',
            json.dumps({'track_id': 'nonexistent_track'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_add_track_missing_id(self):
        """POST without track_id returns 400."""
        response = self.client.post(
            '/ajax/add-track/',
            json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_add_track_get_rejected(self):
        """GET to add-track endpoint returns 405."""
        response = self.client.get('/ajax/add-track/')
        self.assertEqual(response.status_code, 405)


@override_settings(**WEB_TEST_SETTINGS)
class SubmitFeedbackWebTestCase(TestCase):
    """Test submit_feedback web endpoint (POST /api/feedback/)."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="webfb_genre")
        cls.artist = Artist.objects.create(
            id="webfb_artist", name="WebFB Artist", popularity=60
        )
        cls.track = Track.objects.create(
            id="webfb_track", title="WebFB Track",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
        )
        cls.track.genres.add(cls.genre)

    def test_submit_like(self):
        """POST like feedback creates a feedback entry."""
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': True}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'created')
        self.assertEqual(data['score'], 'like')

    def test_submit_dislike(self):
        """POST dislike feedback creates a feedback entry."""
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': False}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'created')
        self.assertEqual(data['score'], 'dislike')

    def test_submit_toggle_undo(self):
        """Same like twice toggles (creates then removes)."""
        self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': True}),
            content_type='application/json',
        )
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': True}),
            content_type='application/json',
        )
        data = response.json()
        self.assertEqual(data['status'], 'removed')

    def test_submit_switch_vote(self):
        """Switching from like to dislike updates feedback."""
        self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': True}),
            content_type='application/json',
        )
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'webfb_track', 'score': False}),
            content_type='application/json',
        )
        data = response.json()
        self.assertEqual(data['status'], 'updated')
        self.assertEqual(data['score'], 'dislike')

    def test_submit_feedback_nonexistent_track(self):
        """Feedback for nonexistent track returns 404."""
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'track_id': 'nonexistent_webfb', 'score': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_submit_feedback_missing_track_id(self):
        """Feedback without track_id returns 400."""
        response = self.client.post(
            '/api/feedback/',
            json.dumps({'score': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
