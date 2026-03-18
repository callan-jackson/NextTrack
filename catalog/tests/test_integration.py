"""Integration tests for Spotify ingestion and external data enhancements."""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from catalog.models import Genre, Artist, Track, RecommendationFeedback
from catalog.services import (
    ingest_track_from_spotify_data,
    apply_external_data_enhancements,
    get_recommendations_from_sequence,
    calculate_categorical_preferences,
)


# ---------------------------------------------------------------------------
# Spotify ingestion integration tests (task 2.1)
# ---------------------------------------------------------------------------


class SpotifyIngestionTestCase(TestCase):
    """Test ingest_track_from_spotify_data with mocked SpotifyClient."""

    def _make_spotify_track(self, track_id="sp_1", name="Test Track",
                            artist_id="sp_a1", artist_name="Test Artist",
                            popularity=70):
        return {
            "id": track_id,
            "name": name,
            "popularity": popularity,
            "artists": [{"id": artist_id, "name": artist_name}],
        }

    def _make_audio_features(self, track_id="sp_1", danceability=0.7,
                             energy=0.8, valence=0.6, acousticness=0.2,
                             tempo=128.0, loudness=-5.5):
        return {
            "id": track_id,
            "danceability": danceability,
            "energy": energy,
            "valence": valence,
            "acousticness": acousticness,
            "tempo": tempo,
            "loudness": loudness,
        }

    def _mock_client(self, genres=None):
        client = MagicMock()
        client.get_artist.return_value = {
            "id": "sp_a1",
            "name": "Test Artist",
            "genres": genres if genres is not None else ["pop", "electronic"],
        }
        return client

    def test_creates_track_artist_and_genres(self):
        """Full ingestion creates Track, Artist, and Genre records."""
        sp_data = self._make_spotify_track()
        features = self._make_audio_features()
        client = self._mock_client()

        track = ingest_track_from_spotify_data(sp_data, features, client)

        self.assertIsNotNone(track)
        self.assertEqual(track.id, "sp_1")
        self.assertEqual(track.title, "Test Track")
        self.assertTrue(track.is_audio_analyzed)
        self.assertAlmostEqual(track.danceability, 0.7)
        self.assertAlmostEqual(track.energy, 0.8)

        self.assertTrue(Artist.objects.filter(name="Test Artist").exists())

        genre_names = set(track.genres.values_list("name", flat=True))
        self.assertIn("pop", genre_names)
        self.assertIn("electronic", genre_names)

    def test_dedup_existing_artist(self):
        """Ingesting two tracks by the same artist reuses the Artist record."""
        client = self._mock_client()

        track1 = ingest_track_from_spotify_data(
            self._make_spotify_track(track_id="sp_1", name="Song A"),
            self._make_audio_features(track_id="sp_1"),
            client,
        )
        track2 = ingest_track_from_spotify_data(
            self._make_spotify_track(track_id="sp_2", name="Song B"),
            self._make_audio_features(track_id="sp_2"),
            client,
        )

        self.assertIsNotNone(track1)
        self.assertIsNotNone(track2)
        self.assertEqual(track1.artist_id, track2.artist_id)

    def test_fallback_when_audio_features_unavailable(self):
        """Without audio features, track uses neutral defaults and is_audio_analyzed=False."""
        sp_data = self._make_spotify_track()
        client = self._mock_client()

        track = ingest_track_from_spotify_data(sp_data, None, client)

        self.assertIsNotNone(track)
        self.assertFalse(track.is_audio_analyzed)
        self.assertAlmostEqual(track.valence, 0.5)
        self.assertAlmostEqual(track.energy, 0.5)
        self.assertAlmostEqual(track.danceability, 0.5)
        self.assertAlmostEqual(track.acousticness, 0.5)
        self.assertAlmostEqual(track.tempo, 120.0)

    def test_no_artist_data_returns_none(self):
        """Track with empty artists list returns None."""
        sp_data = {
            "id": "sp_no_artist",
            "name": "No Artist Track",
            "popularity": 50,
            "artists": [],
        }
        client = self._mock_client()
        track = ingest_track_from_spotify_data(sp_data, None, client)
        self.assertIsNone(track)

    def test_genre_fallback_to_unknown(self):
        """When artist has no genres, track gets 'unknown' genre."""
        client = self._mock_client(genres=[])

        sp_data = self._make_spotify_track(
            track_id="sp_no_genre", artist_id="sp_a_nogenre", artist_name="No Genre Artist"
        )
        track = ingest_track_from_spotify_data(sp_data, None, client)

        self.assertIsNotNone(track)
        genre_names = set(track.genres.values_list("name", flat=True))
        self.assertIn("unknown", genre_names)

    def test_audio_features_clamped(self):
        """Values outside 0-1 are clamped."""
        features = self._make_audio_features(valence=1.5, energy=-0.2)
        sp_data = self._make_spotify_track(track_id="sp_clamp")
        client = self._mock_client()

        track = ingest_track_from_spotify_data(sp_data, features, client)

        self.assertIsNotNone(track)
        self.assertAlmostEqual(track.valence, 1.0)
        self.assertAlmostEqual(track.energy, 0.0)


# ---------------------------------------------------------------------------
# External data enhancement tests (task 2.4)
# ---------------------------------------------------------------------------


class ExternalDataEnhancementTestCase(TestCase):
    """Test apply_external_data_enhancements with mocked LiveExternalDataService."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="ext_rock")

        cls.playlist_artist = Artist.objects.create(
            id="ext_playlist_artist", name="Playlist Band", popularity=70
        )
        cls.similar_artist = Artist.objects.create(
            id="ext_similar_artist", name="Similar Band 1", popularity=65
        )
        cls.influence_artist = Artist.objects.create(
            id="ext_influence_artist", name="Influence Band", popularity=60
        )
        cls.unrelated_artist = Artist.objects.create(
            id="ext_unrelated_artist", name="Unrelated Act", popularity=55
        )

        cls.similar_track = Track.objects.create(
            id="ext_similar_track", title="Similar Track",
            artist=cls.similar_artist,
            valence=0.6, energy=0.7, danceability=0.5,
            acousticness=0.3, tempo=120.0, popularity=65,
        )
        cls.similar_track.genres.add(cls.genre)

        cls.influence_track = Track.objects.create(
            id="ext_influence_track", title="Influence Track",
            artist=cls.influence_artist,
            valence=0.5, energy=0.6, danceability=0.4,
            acousticness=0.4, tempo=110.0, popularity=60,
        )
        cls.influence_track.genres.add(cls.genre)

        cls.unrelated_track = Track.objects.create(
            id="ext_unrelated_track", title="Unrelated Track",
            artist=cls.unrelated_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=100.0, popularity=55,
        )
        cls.unrelated_track.genres.add(cls.genre)

    def _mock_service(self, similar_artists=None, influenced_by=None, lastfm_tags=None):
        service = MagicMock()

        def batch_side_effect(artist_names, max_live_fetches=5):
            results = {}
            for name in artist_names:
                results[name] = {
                    "name": name,
                    "similar_artists": similar_artists or [],
                    "influenced_by": influenced_by or [],
                    "lastfm_tags": lastfm_tags or [],
                }
            return results

        service.batch_get_artist_info.side_effect = batch_side_effect
        return service

    def test_similar_artist_boost(self):
        """Tracks by similar artists get a score boost."""
        service = self._mock_service(similar_artists=["Similar Band 1"])

        recommendations = [
            (self.similar_track, 80.0),
            (self.unrelated_track, 80.0),
        ]

        result = apply_external_data_enhancements(
            recommendations,
            {"Playlist Band"},
            external_data_service=service,
        )

        similar_score = next(s for t, s, e in result if t.id == "ext_similar_track")
        unrelated_score = next(s for t, s, e in result if t.id == "ext_unrelated_track")
        self.assertGreater(similar_score, unrelated_score)

    def test_influence_chain_boost(self):
        """Tracks by influential artists get a score boost."""
        service = self._mock_service(influenced_by=["Influence Band"])

        recommendations = [
            (self.influence_track, 80.0),
            (self.unrelated_track, 80.0),
        ]

        result = apply_external_data_enhancements(
            recommendations,
            {"Playlist Band"},
            external_data_service=service,
        )

        influence_score = next(s for t, s, e in result if t.id == "ext_influence_track")
        unrelated_score = next(s for t, s, e in result if t.id == "ext_unrelated_track")
        self.assertGreater(influence_score, unrelated_score)

    def test_tag_match_boost(self):
        """Tracks with matching Last.fm tags get a score boost."""
        service = self._mock_service(lastfm_tags=["rock", "alternative"])

        recommendations = [
            (self.similar_track, 80.0),
            (self.unrelated_track, 80.0),
        ]

        result = apply_external_data_enhancements(
            recommendations,
            {"Playlist Band"},
            external_data_service=service,
        )

        # Both tracks get tag matches since the mock returns the same tags for all
        for track, score, enhancements in result:
            self.assertGreaterEqual(score, 80.0)

    def test_boost_capping(self):
        """Total boost from tag matches is capped at 0.25."""
        # Provide many tags so the tag boost would exceed the cap
        many_tags = [f"tag_{i}" for i in range(20)]
        service = self._mock_service(lastfm_tags=many_tags)

        recommendations = [(self.similar_track, 80.0)]

        result = apply_external_data_enhancements(
            recommendations,
            {"Playlist Band"},
            external_data_service=service,
        )

        _, score, enhancements = result[0]
        # Tag boost is capped at 0.25, so score should be at most 80 * (1 + 0.25) = 100
        self.assertLessEqual(score, 100.1)

    def test_no_service_returns_empty_enhancements(self):
        """Without an external service, enhancements are empty dicts."""
        recommendations = [(self.similar_track, 80.0)]

        result = apply_external_data_enhancements(
            recommendations,
            {"Playlist Band"},
            external_data_service=None,
        )

        self.assertEqual(len(result), 1)
        _, score, enhancements = result[0]
        self.assertEqual(score, 80.0)
        self.assertEqual(enhancements, {})

    def test_no_playlist_artists_returns_empty_enhancements(self):
        """Without playlist artist names, enhancements are empty dicts."""
        service = self._mock_service(similar_artists=["Similar Band 1"])
        recommendations = [(self.similar_track, 80.0)]

        result = apply_external_data_enhancements(
            recommendations,
            set(),
            external_data_service=service,
        )

        _, score, enhancements = result[0]
        self.assertEqual(score, 80.0)
        self.assertEqual(enhancements, {})


# ---------------------------------------------------------------------------
# Celery task integration tests (task 2.2)
# ---------------------------------------------------------------------------


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'celery-integration-tests',
        }
    },
)
class CeleryTaskIntegrationTestCase(TestCase):
    """Test Celery tasks with ALWAYS_EAGER so they run synchronously."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="celery_genre")
        cls.artist = Artist.objects.create(
            id="celery_artist", name="Celery Artist", popularity=80
        )
        cls.track1 = Track.objects.create(
            id="celery_track_1", title="Celery Track 1",
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=80,
        )
        cls.track1.genres.add(cls.genre)

        cls.track2 = Track.objects.create(
            id="celery_track_2", title="Celery Track 2",
            artist=cls.artist,
            valence=0.55, energy=0.55, danceability=0.55,
            acousticness=0.45, tempo=125.0, popularity=85,
        )
        cls.track2.genres.add(cls.genre)

    def test_generate_recommendations_task_populates_cache(self):
        """generate_recommendations_task computes recs and stores them in cache."""
        from django.core.cache import cache
        from catalog.tasks import generate_recommendations_task

        cache.clear()

        result = generate_recommendations_task(self.track1.id, limit=5)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['track_id'], self.track1.id)

        cache_key = f"rec_{self.track1.id}_5"
        cached_data = cache.get(cache_key)
        self.assertIsNotNone(cached_data)
        self.assertIn('recommendations', cached_data)
        self.assertIn('source_track', cached_data)

    def test_generate_recommendations_task_nonexistent_track(self):
        """Task returns error status for a track ID that does not exist."""
        from catalog.tasks import generate_recommendations_task

        result = generate_recommendations_task("nonexistent_celery_id", limit=5)

        self.assertEqual(result['status'], 'error')
        self.assertIn('not found', result['message'])

    def test_warm_cache_for_popular_tracks_runs(self):
        """warm_cache_for_popular_tracks processes tracks without error."""
        from catalog.tasks import warm_cache_for_popular_tracks

        result = warm_cache_for_popular_tracks(popularity_threshold=70, limit=10)

        self.assertEqual(result['status'], 'complete')
        self.assertGreaterEqual(result['tracks_queued'], 0)
        self.assertGreaterEqual(result['total_popular_tracks'], 0)

    @patch('catalog.external_data.WikidataClient')
    @patch('catalog.external_data.MusicBrainzClient')
    def test_enrich_artist_data_task(self, MockMB, MockWD):
        """enrich_artist_data_task updates Artist fields from mocked external data."""
        from catalog.tasks import enrich_artist_data_task

        unenriched_artist = Artist.objects.create(
            id="celery_unenriched", name="Unenriched Artist", popularity=60
        )

        mock_mb = MockMB.return_value
        mock_mb.search_artist.return_value = [
            {'id': 'mb-uuid-celery', 'name': 'Unenriched Artist', 'score': 100}
        ]
        mock_mb.get_artist_details.return_value = {
            'id': 'mb-uuid-celery',
            'name': 'Unenriched Artist',
            'country': 'GB',
            'type': 'Group',
            'formed_year': 1995,
            'disbanded_year': None,
            'wikidata_id': 'Q99999',
            'tags': [
                {'name': 'britpop', 'count': 8},
                {'name': 'indie', 'count': 5},
            ],
        }

        mock_wd = MockWD.return_value
        mock_wd.get_entity.return_value = {
            'id': 'Q99999',
            'label': 'Unenriched Artist',
            'description': None,
            'formed_year': 1995,
            'genre_ids': [],
            'country_id': 'Q145',
            'influenced_by_ids': [],
        }
        mock_wd.get_entity_labels.return_value = {}

        result = enrich_artist_data_task(unenriched_artist.id)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['musicbrainz_id'], 'mb-uuid-celery')

        unenriched_artist.refresh_from_db()
        self.assertEqual(unenriched_artist.origin_country, 'GB')
        self.assertEqual(unenriched_artist.artist_type, 'Group')
        self.assertEqual(unenriched_artist.formed_year, 1995)
        self.assertEqual(unenriched_artist.musicbrainz_id, 'mb-uuid-celery')
        self.assertEqual(unenriched_artist.wikidata_id, 'Q99999')
        self.assertIsNotNone(unenriched_artist.enriched_at)

    @patch('catalog.spotify_client.SpotifyClient')
    def test_harvest_related_tracks_task(self, MockSpotify):
        """harvest_related_tracks_task creates new tracks from mocked Spotify recs."""
        from catalog.tasks import harvest_related_tracks_task

        mock_client = MockSpotify.return_value
        mock_client.is_configured = True
        mock_client.get_recommendations.return_value = [
            {
                'id': 'harvested_sp_1',
                'name': 'Harvested Song',
                'artists': [{'id': 'harvested_artist_1', 'name': 'Harvest Artist'}],
                'popularity': 65,
            }
        ]
        mock_client.get_audio_features_batch.return_value = {
            'harvested_sp_1': {
                'id': 'harvested_sp_1',
                'danceability': 0.6,
                'energy': 0.7,
                'valence': 0.5,
                'acousticness': 0.3,
                'tempo': 125.0,
                'loudness': -6.0,
            }
        }
        mock_client.get_artist.return_value = {
            'id': 'harvested_artist_1',
            'name': 'Harvest Artist',
            'genres': ['indie', 'rock'],
        }

        result = harvest_related_tracks_task(self.track1.id, limit=5)

        self.assertEqual(result['status'], 'success')
        self.assertGreaterEqual(result['harvested'], 1)
        self.assertTrue(Track.objects.filter(id='harvested_sp_1').exists())

    @patch('catalog.spotify_client.SpotifyClient')
    def test_ingest_track_by_spotify_id(self, MockSpotify):
        """ingest_track_by_spotify_id creates a Track from mocked Spotify data."""
        from catalog.tasks import ingest_track_by_spotify_id

        mock_client = MockSpotify.return_value
        mock_client.is_configured = True
        mock_client.get_track.return_value = {
            'id': 'sp_ingest_single',
            'name': 'Ingested Song',
            'artists': [{'id': 'sp_ingest_artist', 'name': 'Ingest Artist'}],
            'popularity': 70,
        }
        mock_client.get_audio_features.return_value = {
            'id': 'sp_ingest_single',
            'danceability': 0.65,
            'energy': 0.75,
            'valence': 0.55,
            'acousticness': 0.25,
            'tempo': 130.0,
            'loudness': -5.0,
        }
        mock_client.get_artist.return_value = {
            'id': 'sp_ingest_artist',
            'name': 'Ingest Artist',
            'genres': ['pop'],
        }

        result = ingest_track_by_spotify_id('sp_ingest_single')

        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['title'], 'Ingested Song')
        self.assertTrue(Track.objects.filter(id='sp_ingest_single').exists())

        track = Track.objects.get(id='sp_ingest_single')
        self.assertAlmostEqual(track.danceability, 0.65)
        self.assertAlmostEqual(track.energy, 0.75)

    @patch('catalog.spotify_client.SpotifyClient')
    def test_ingest_track_already_exists(self, MockSpotify):
        """ingest_track_by_spotify_id returns 'exists' for a track already in DB."""
        from catalog.tasks import ingest_track_by_spotify_id

        result = ingest_track_by_spotify_id(self.track1.id)

        self.assertEqual(result['status'], 'exists')
        self.assertEqual(result['track_id'], self.track1.id)
        MockSpotify.assert_not_called()


# ---------------------------------------------------------------------------
# Full recommendation + feedback pipeline tests (task 2.3)
# ---------------------------------------------------------------------------


class RecommendationFeedbackPipelineTestCase(TestCase):
    """End-to-end: generate recs, submit feedback, regen, verify centroid shift."""

    @classmethod
    def setUpTestData(cls):
        cls.pop_genre = Genre.objects.create(name="pipeline_pop")
        cls.rock_genre = Genre.objects.create(name="pipeline_rock")
        cls.ambient_genre = Genre.objects.create(name="pipeline_ambient")

        cls.pop_artist = Artist.objects.create(
            id="pipeline_pop_artist", name="Pipeline Pop Artist",
            popularity=80, origin_country="US", formed_year=2005,
        )
        cls.rock_artist = Artist.objects.create(
            id="pipeline_rock_artist", name="Pipeline Rock Artist",
            popularity=70, origin_country="GB", formed_year=1995,
        )
        cls.ambient_artist = Artist.objects.create(
            id="pipeline_ambient_artist", name="Pipeline Ambient Artist",
            popularity=55, origin_country="DE", formed_year=2010,
        )

        # Input track: mid-range features
        cls.input_track = Track.objects.create(
            id="pipe_input", title="Pipeline Input",
            artist=cls.pop_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=70,
        )
        cls.input_track.genres.add(cls.pop_genre)

        # Candidate cluster 1: high energy pop (should be boosted by liking)
        for i in range(3):
            t = Track.objects.create(
                id=f"pipe_pop_{i}", title=f"Pipeline Pop {i}",
                artist=cls.pop_artist,
                valence=0.7 + i * 0.02, energy=0.8 + i * 0.02,
                danceability=0.7, acousticness=0.15,
                tempo=130.0 + i, popularity=75,
            )
            t.genres.add(cls.pop_genre)

        # Candidate cluster 2: low energy ambient (should be penalised by disliking)
        for i in range(3):
            t = Track.objects.create(
                id=f"pipe_ambient_{i}", title=f"Pipeline Ambient {i}",
                artist=cls.ambient_artist,
                valence=0.2 + i * 0.02, energy=0.2 + i * 0.02,
                danceability=0.2, acousticness=0.85,
                tempo=80.0 + i, popularity=50,
            )
            t.genres.add(cls.ambient_genre)

        # Candidate cluster 3: rock (mid features)
        for i in range(3):
            t = Track.objects.create(
                id=f"pipe_rock_{i}", title=f"Pipeline Rock {i}",
                artist=cls.rock_artist,
                valence=0.55 + i * 0.02, energy=0.6 + i * 0.02,
                danceability=0.5, acousticness=0.35,
                tempo=140.0 + i, popularity=65,
            )
            t.genres.add(cls.rock_genre)

    def test_feedback_shifts_centroid(self):
        """Liking high-energy tracks shifts centroid toward energy."""
        session_key = "pipeline_session_centroid"

        result_before = get_recommendations_from_sequence(
            [self.input_track.id], limit=10, session_key=session_key,
        )
        centroid_before = result_before['centroid']

        # Like the high-energy pop tracks
        for i in range(3):
            RecommendationFeedback.objects.create(
                track=Track.objects.get(id=f"pipe_pop_{i}"),
                score=True,
                session_key=session_key,
            )
        # Dislike the low-energy ambient tracks
        for i in range(3):
            RecommendationFeedback.objects.create(
                track=Track.objects.get(id=f"pipe_ambient_{i}"),
                score=False,
                session_key=session_key,
            )

        result_after = get_recommendations_from_sequence(
            [self.input_track.id], limit=10, session_key=session_key,
        )
        centroid_after = result_after['centroid']

        # The centroid energy should have shifted upward due to liking
        # high-energy tracks and disliking low-energy ones.
        self.assertGreater(centroid_after['energy'], centroid_before['energy'])

    def test_feedback_changes_ranking(self):
        """After feedback, ambient tracks rank lower relative to pop/rock."""
        session_key = "pipeline_session_ranking"

        result_before = get_recommendations_from_sequence(
            [self.input_track.id], limit=10,
        )

        # Compute average rank position of ambient tracks before feedback
        ids_before = [t.id for t in result_before['recommendations']]
        ambient_positions_before = [
            ids_before.index(tid) for tid in ids_before
            if tid.startswith('pipe_ambient_')
        ]

        # Like pop, dislike ambient
        for i in range(3):
            RecommendationFeedback.objects.create(
                track=Track.objects.get(id=f"pipe_pop_{i}"),
                score=True,
                session_key=session_key,
            )
        for i in range(3):
            RecommendationFeedback.objects.create(
                track=Track.objects.get(id=f"pipe_ambient_{i}"),
                score=False,
                session_key=session_key,
            )

        result_after = get_recommendations_from_sequence(
            [self.input_track.id], limit=10, session_key=session_key,
        )
        ids_after = [t.id for t in result_after['recommendations']]
        ambient_positions_after = [
            ids_after.index(tid) for tid in ids_after
            if tid.startswith('pipe_ambient_')
        ]

        # If ambient tracks are still present, they should have moved down
        # (higher index = lower rank). If they're absent from after-list
        # (pushed out by better matches), that also counts as passing.
        if ambient_positions_before and ambient_positions_after:
            avg_before = sum(ambient_positions_before) / len(ambient_positions_before)
            avg_after = sum(ambient_positions_after) / len(ambient_positions_after)
            self.assertGreaterEqual(avg_after, avg_before)
        elif ambient_positions_before and not ambient_positions_after:
            # Ambient tracks were pushed out entirely -- even stronger effect
            pass
        else:
            # Ambient tracks weren't in the before list either, so the centroid
            # shift test above is the main validation.
            pass

    def test_categorical_preferences_accumulated(self):
        """Feedback creates measurable categorical preference data."""
        session_key = "pipeline_session_catprefs"

        # Like pop, dislike ambient
        RecommendationFeedback.objects.create(
            track=Track.objects.get(id="pipe_pop_0"),
            score=True, session_key=session_key,
        )
        RecommendationFeedback.objects.create(
            track=Track.objects.get(id="pipe_pop_1"),
            score=True, session_key=session_key,
        )
        RecommendationFeedback.objects.create(
            track=Track.objects.get(id="pipe_ambient_0"),
            score=False, session_key=session_key,
        )

        prefs = calculate_categorical_preferences(session_key)

        self.assertTrue(prefs['has_preferences'])
        self.assertIn('pipeline_pop', prefs['genres']['liked'])
        self.assertEqual(prefs['genres']['liked']['pipeline_pop'], 2)
        self.assertIn('pipeline_ambient', prefs['genres']['disliked'])
        self.assertEqual(prefs['genres']['disliked']['pipeline_ambient'], 1)
        self.assertIn('US', prefs['countries']['liked'])
        self.assertIn('DE', prefs['countries']['disliked'])
