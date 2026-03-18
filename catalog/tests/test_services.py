"""Unit tests for catalog.services (recommendation engine, feature vectors, diversity)."""

import math
from unittest.mock import patch, MagicMock

import numpy as np
from django.test import TestCase

from catalog.models import Genre, Artist, Track, RecommendationFeedback
from catalog.services import (
    get_feature_vector,
    euclidean_distance,
    calculate_centroid,
    apply_preferences,
    get_recommendations_from_sequence,
    calculate_similarity,
    get_candidates_with_serendipity,
    _compute_diversity_score,
    calculate_categorical_preferences,
    apply_categorical_preferences,
    get_enhanced_recommendations,
    _generate_explanations,
    search_tracks,
)


# ---------------------------------------------------------------------------
# Existing tests migrated from catalog/tests.py
# ---------------------------------------------------------------------------


class EuclideanDistanceTestCase(TestCase):
    """Test Euclidean distance calculation."""

    def test_euclidean_distance_identical_vectors(self):
        """Distance between identical vectors should be 0."""
        vec = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        result = euclidean_distance(vec, vec)
        self.assertEqual(result, 0.0)

    def test_euclidean_distance_known_values(self):
        """Test with known mathematical values."""
        vec_a = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        vec_b = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
        result = euclidean_distance(vec_a, vec_b)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_euclidean_distance_3_4_5_triangle(self):
        """Test with classic 3-4-5 right triangle."""
        vec_a = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        vec_b = np.array([0.3, 0.4, 0.0, 0.0, 0.0])
        result = euclidean_distance(vec_a, vec_b)
        self.assertAlmostEqual(result, 0.5, places=5)


class CentroidCalculationTestCase(TestCase):
    """Test centroid (mean vector) calculation."""

    def test_centroid_single_vector(self):
        """Centroid of one vector is the vector itself."""
        vec = np.array([0.5, 0.6, 0.7, 0.8, 0.9])
        centroid = calculate_centroid([vec])
        np.testing.assert_array_almost_equal(centroid, vec)

    def test_centroid_multiple_vectors(self):
        """Test centroid of multiple vectors."""
        vec_a = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        vec_b = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        centroid = calculate_centroid([vec_a, vec_b])
        expected = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        np.testing.assert_array_almost_equal(centroid, expected)

    def test_centroid_empty_list(self):
        """Centroid of empty list is zero vector."""
        centroid = calculate_centroid([])
        expected = np.zeros(5)
        np.testing.assert_array_almost_equal(centroid, expected)


class PreferencesApplicationTestCase(TestCase):
    """Test preference weighting on centroid."""

    def test_apply_preferences_empty(self):
        """No preferences returns original centroid."""
        centroid = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        result = apply_preferences(centroid, None)
        np.testing.assert_array_almost_equal(result, centroid)

    def test_apply_preferences_single(self):
        """Single preference weights centroid."""
        centroid = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        preferences = {'energy': 0.9}
        result = apply_preferences(centroid, preferences)
        self.assertAlmostEqual(result[1], 0.7, places=5)
        self.assertAlmostEqual(result[0], 0.5, places=5)

    def test_apply_preferences_multiple(self):
        """Multiple preferences are applied."""
        centroid = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        preferences = {'energy': 0.9, 'valence': 0.1}
        result = apply_preferences(centroid, preferences)
        self.assertAlmostEqual(result[0], 0.3, places=5)
        self.assertAlmostEqual(result[1], 0.7, places=5)


class RecommendationEngineTestCase(TestCase):
    """Test the recommendation engine finds closest tracks."""

    @classmethod
    def setUpTestData(cls):
        """Create test data: 3 tracks with known feature values."""
        cls.genre = Genre.objects.create(name='test_genre')
        cls.artist = Artist.objects.create(
            id='test_artist_1',
            name='Test Artist',
            popularity=50
        )

        cls.track_a = Track.objects.create(
            id='track_a',
            title='Track A (Target)',
            artist=cls.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        cls.track_a.genres.add(cls.genre)

        cls.track_b = Track.objects.create(
            id='track_b',
            title='Track B (Very Similar)',
            artist=cls.artist,
            valence=0.51,
            energy=0.51,
            danceability=0.51,
            acousticness=0.51,
            tempo=102.0,
            popularity=50
        )
        cls.track_b.genres.add(cls.genre)

        cls.track_c = Track.objects.create(
            id='track_c',
            title='Track C (Different)',
            artist=cls.artist,
            valence=0.9,
            energy=0.9,
            danceability=0.9,
            acousticness=0.1,
            tempo=180.0,
            popularity=50
        )
        cls.track_c.genres.add(cls.genre)

    def test_feature_vector_extraction(self):
        """Feature vectors are correctly extracted."""
        vector = get_feature_vector(self.track_a)
        self.assertEqual(len(vector), 5)
        self.assertAlmostEqual(vector[0], 0.5, places=5)
        self.assertAlmostEqual(vector[4], 0.5, places=5)

    def test_tempo_normalization(self):
        """Tempo is normalized by dividing by 200."""
        vector = get_feature_vector(self.track_c)
        self.assertAlmostEqual(vector[4], 0.9, places=5)

    def test_recommendations_from_sequence_single(self):
        """Recommendations from a single track return closest match."""
        result = get_recommendations_from_sequence([self.track_a.id], limit=10)

        self.assertIn('recommendations', result)
        self.assertIn('centroid', result)
        self.assertIn('input_tracks', result)
        self.assertGreater(len(result['recommendations']), 0)
        self.assertEqual(result['recommendations'][0].id, 'track_b')

    def test_recommendations_from_sequence_multiple(self):
        """Recommendations from multiple tracks use averaged centroid."""
        result = get_recommendations_from_sequence(
            [self.track_a.id, self.track_b.id],
            limit=10
        )

        self.assertEqual(len(result['input_tracks']), 2)
        self.assertIn('centroid', result)

    def test_recommendations_with_preferences(self):
        """Preferences shift the centroid and affect results."""
        result_no_pref = get_recommendations_from_sequence(
            [self.track_a.id],
            limit=10
        )

        result_with_pref = get_recommendations_from_sequence(
            [self.track_a.id],
            preferences={'energy': 0.95, 'valence': 0.95},
            limit=10
        )

        self.assertNotEqual(
            result_no_pref['centroid']['energy'],
            result_with_pref['centroid']['energy']
        )

    def test_recommendations_exclude_input_tracks(self):
        """Input tracks are not in recommendations."""
        result = get_recommendations_from_sequence(
            [self.track_a.id, self.track_b.id],
            limit=10
        )
        rec_ids = [r.id for r in result['recommendations']]
        self.assertNotIn(self.track_a.id, rec_ids)
        self.assertNotIn(self.track_b.id, rec_ids)

    def test_recommendations_empty_input(self):
        """Empty input returns empty result."""
        result = get_recommendations_from_sequence([], limit=10)
        self.assertEqual(result['recommendations'], [])

    def test_recommendations_nonexistent_tracks(self):
        """Nonexistent track IDs return empty result."""
        result = get_recommendations_from_sequence(['fake_id_1', 'fake_id_2'])
        self.assertEqual(result['recommendations'], [])


class RecommendationWithFeedbackTestCase(TestCase):
    """Test recommendations with user feedback."""

    @classmethod
    def setUpTestData(cls):
        """Create diverse test data for feedback-influenced recommendations."""
        cls.rock_genre = Genre.objects.create(name='rock')
        cls.pop_genre = Genre.objects.create(name='pop')
        cls.jazz_genre = Genre.objects.create(name='jazz')

        cls.artist1 = Artist.objects.create(id='artist_rock', name='Rock Artist', popularity=70)
        cls.artist2 = Artist.objects.create(id='artist_pop', name='Pop Artist', popularity=80)
        cls.artist3 = Artist.objects.create(id='artist_jazz', name='Jazz Artist', popularity=60)

        cls.input_track = Track.objects.create(
            id='input_track',
            title='Input Rock Track',
            artist=cls.artist1,
            valence=0.6,
            energy=0.8,
            danceability=0.5,
            acousticness=0.2,
            tempo=130.0,
            popularity=75
        )
        cls.input_track.genres.add(cls.rock_genre)

        cls.similar_rock = Track.objects.create(
            id='similar_rock',
            title='Similar Rock',
            artist=cls.artist1,
            valence=0.65,
            energy=0.75,
            danceability=0.55,
            acousticness=0.25,
            tempo=125.0,
            popularity=70
        )
        cls.similar_rock.genres.add(cls.rock_genre)

        cls.pop_track = Track.objects.create(
            id='pop_track',
            title='Pop Track',
            artist=cls.artist2,
            valence=0.8,
            energy=0.6,
            danceability=0.9,
            acousticness=0.1,
            tempo=115.0,
            popularity=85
        )
        cls.pop_track.genres.add(cls.pop_genre)

        cls.jazz_track = Track.objects.create(
            id='jazz_track',
            title='Jazz Track',
            artist=cls.artist3,
            valence=0.4,
            energy=0.3,
            danceability=0.2,
            acousticness=0.8,
            tempo=90.0,
            popularity=55
        )
        cls.jazz_track.genres.add(cls.jazz_genre)

    def test_recommendations_without_feedback(self):
        """Baseline recommendations return closest match first."""
        result = get_recommendations_from_sequence([self.input_track.id], limit=10)
        self.assertIn('recommendations', result)
        if result['recommendations']:
            self.assertEqual(result['recommendations'][0].id, 'similar_rock')

    def test_similarity_calculation(self):
        """Similar tracks have high similarity score."""
        distance = euclidean_distance(
            get_feature_vector(self.input_track),
            get_feature_vector(self.similar_rock)
        )
        similarity = max(0, 1 - distance) * 100
        self.assertGreater(similarity, 80)

    def test_similarity_low_for_different_tracks(self):
        """Different tracks have lower similarity score."""
        distance = euclidean_distance(
            get_feature_vector(self.input_track),
            get_feature_vector(self.jazz_track)
        )
        similarity = max(0, 1 - distance) * 100
        self.assertLess(similarity, 70)


class EdgeCaseTestCase(TestCase):
    """Test edge cases and boundary conditions."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='edge_case_genre')
        cls.artist = Artist.objects.create(
            id='edge_case_artist',
            name='Edge Case Artist',
            popularity=50
        )

    def test_empty_playlist_recommendations(self):
        """Empty input returns empty result."""
        result = get_recommendations_from_sequence([], limit=10)
        self.assertEqual(result['recommendations'], [])
        self.assertEqual(result['input_tracks'], [])

    def test_single_track_recommendations(self):
        """Single track input produces valid centroid."""
        track = Track.objects.create(
            id='single_edge',
            title='Single Edge',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        track.genres.add(self.genre)

        result = get_recommendations_from_sequence([track.id], limit=10)
        self.assertIn('centroid', result)
        self.assertEqual(len(result['input_tracks']), 1)

    def test_duplicate_track_ids_in_input(self):
        """Duplicate track IDs are handled gracefully."""
        track = Track.objects.create(
            id='dup_edge',
            title='Duplicate Edge',
            artist=self.artist,
            valence=0.6,
            energy=0.6,
            danceability=0.6,
            acousticness=0.4,
            tempo=110.0,
            popularity=55
        )
        track.genres.add(self.genre)

        result = get_recommendations_from_sequence(
            [track.id, track.id, track.id],
            limit=10
        )
        self.assertIn('centroid', result)

    def test_extreme_feature_values(self):
        """Tracks with extreme values still produce recommendations."""
        max_track = Track.objects.create(
            id='max_edge',
            title='Maximum Edge',
            artist=self.artist,
            valence=1.0,
            energy=1.0,
            danceability=1.0,
            acousticness=1.0,
            tempo=200.0,
            popularity=100
        )
        max_track.genres.add(self.genre)

        min_track = Track.objects.create(
            id='min_edge',
            title='Minimum Edge',
            artist=self.artist,
            valence=0.0,
            energy=0.0,
            danceability=0.0,
            acousticness=0.0,
            tempo=0.0,
            popularity=0
        )
        min_track.genres.add(self.genre)

        result = get_recommendations_from_sequence([max_track.id], limit=5)
        self.assertIn('recommendations', result)

    def test_limit_boundary_values(self):
        """Limit parameter correctly caps results."""
        track = Track.objects.create(
            id='limit_edge',
            title='Limit Edge',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        track.genres.add(self.genre)

        for i in range(5):
            t = Track.objects.create(
                id=f'limit_rec_{i}',
                title=f'Limit Rec {i}',
                artist=self.artist,
                valence=0.5 + (i * 0.02),
                energy=0.5 + (i * 0.02),
                danceability=0.5,
                acousticness=0.5,
                tempo=100.0,
                popularity=50
            )
            t.genres.add(self.genre)

        result = get_recommendations_from_sequence([track.id], limit=1)
        self.assertEqual(len(result['recommendations']), 1)

        result = get_recommendations_from_sequence([track.id], limit=0)
        self.assertEqual(len(result['recommendations']), 0)

    def test_preferences_boundary_values(self):
        """Extreme preference values are handled correctly."""
        track = Track.objects.create(
            id='pref_edge',
            title='Preference Edge',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        track.genres.add(self.genre)

        result = get_recommendations_from_sequence(
            [track.id],
            preferences={'energy': 1.0, 'valence': 1.0, 'danceability': 1.0},
            limit=5
        )
        self.assertIn('centroid', result)

        result = get_recommendations_from_sequence(
            [track.id],
            preferences={'energy': 0.0, 'valence': 0.0, 'danceability': 0.0},
            limit=5
        )
        self.assertIn('centroid', result)


# ---------------------------------------------------------------------------
# New service tests (tasks 1.1, 1.5, 1.6)
# ---------------------------------------------------------------------------


class GetFeatureVectorBoundaryTestCase(TestCase):
    """Test get_feature_vector with boundary and edge-case inputs."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='fv_test_genre')
        cls.artist = Artist.objects.create(
            id='fv_artist', name='FV Artist', popularity=50
        )

    def test_zero_tempo(self):
        """Track with tempo=0 should produce 0 in the tempo slot."""
        track = Track.objects.create(
            id='fv_zero_tempo',
            title='Zero Tempo Track',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=0.0,
            popularity=50
        )
        vec = get_feature_vector(track)
        self.assertEqual(len(vec), 5)
        self.assertAlmostEqual(vec[4], 0.0, places=5)

    def test_high_tempo_normalization(self):
        """Tempo of 200 should normalize to 1.0."""
        track = Track.objects.create(
            id='fv_high_tempo',
            title='High Tempo Track',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=200.0,
            popularity=50
        )
        vec = get_feature_vector(track)
        self.assertAlmostEqual(vec[4], 1.0, places=5)

    def test_all_zeros(self):
        """All-zero features produce a zero vector (except for dtype)."""
        track = Track.objects.create(
            id='fv_all_zeros',
            title='All Zeros Track',
            artist=self.artist,
            valence=0.0,
            energy=0.0,
            danceability=0.0,
            acousticness=0.0,
            tempo=0.0,
            popularity=0
        )
        vec = get_feature_vector(track)
        np.testing.assert_array_almost_equal(vec, np.zeros(5))

    def test_all_max(self):
        """All-max features produce [1,1,1,1,1]."""
        track = Track.objects.create(
            id='fv_all_max',
            title='All Max Track',
            artist=self.artist,
            valence=1.0,
            energy=1.0,
            danceability=1.0,
            acousticness=1.0,
            tempo=200.0,
            popularity=100
        )
        vec = get_feature_vector(track)
        np.testing.assert_array_almost_equal(vec, np.ones(5))

    def test_vector_dtype_is_float64(self):
        """Returned vector should be float64."""
        track = Track.objects.create(
            id='fv_dtype',
            title='Dtype Track',
            artist=self.artist,
            valence=0.5,
            energy=0.5,
            danceability=0.5,
            acousticness=0.5,
            tempo=100.0,
            popularity=50
        )
        vec = get_feature_vector(track)
        self.assertEqual(vec.dtype, np.float64)


class ComputeDiversityScoreTestCase(TestCase):
    """Test _compute_diversity_score (entropy-based 0-100 score)."""

    def test_uniform_distribution(self):
        """Perfectly uniform distribution should score high."""
        countries = {'US': 5, 'GB': 5, 'JP': 5, 'DE': 5}
        decades = {'1980s': 5, '1990s': 5, '2000s': 5, '2010s': 5}
        types = {'Person': 10, 'Group': 10}
        score = _compute_diversity_score(countries, decades, types)
        # Uniform across all dimensions -> score should be near 100
        self.assertGreater(score, 80)

    def test_single_item_all_dimensions(self):
        """Single category in each dimension scores 0."""
        countries = {'US': 10}
        decades = {'1990s': 10}
        types = {'Group': 10}
        score = _compute_diversity_score(countries, decades, types)
        self.assertEqual(score, 0)

    def test_skewed_distribution(self):
        """Heavily skewed distribution scores lower than uniform."""
        countries_skewed = {'US': 100, 'GB': 1}
        decades_skewed = {'1990s': 100, '2000s': 1}
        types_skewed = {'Person': 100, 'Group': 1}
        score_skewed = _compute_diversity_score(
            countries_skewed, decades_skewed, types_skewed
        )

        countries_uniform = {'US': 50, 'GB': 50}
        decades_uniform = {'1990s': 50, '2000s': 50}
        types_uniform = {'Person': 50, 'Group': 50}
        score_uniform = _compute_diversity_score(
            countries_uniform, decades_uniform, types_uniform
        )

        self.assertLess(score_skewed, score_uniform)

    def test_empty_distributions(self):
        """All empty distributions score 0."""
        score = _compute_diversity_score({}, {}, {})
        self.assertEqual(score, 0)

    def test_partial_empty(self):
        """Some empty dimensions still produce a valid score."""
        countries = {'US': 5, 'GB': 5}
        score = _compute_diversity_score(countries, {}, {})
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)


class GetCandidatesWithSerendipityTestCase(TestCase):
    """Test get_candidates_with_serendipity genre split and input exclusion."""

    @classmethod
    def setUpTestData(cls):
        cls.rock = Genre.objects.create(name='seren_rock')
        cls.pop = Genre.objects.create(name='seren_pop')
        cls.artist = Artist.objects.create(
            id='seren_artist', name='Serendipity Artist', popularity=80
        )

        # Input track (rock)
        cls.input_track = Track.objects.create(
            id='seren_input',
            title='Input Track',
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=70
        )
        cls.input_track.genres.add(cls.rock)

        # Rock candidate
        cls.rock_candidate = Track.objects.create(
            id='seren_rock_cand',
            title='Rock Candidate',
            artist=cls.artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=60
        )
        cls.rock_candidate.genres.add(cls.rock)

        # Pop candidate (high popularity for serendipity discovery)
        cls.pop_candidate = Track.objects.create(
            id='seren_pop_cand',
            title='Pop Candidate',
            artist=cls.artist,
            valence=0.8, energy=0.8, danceability=0.8,
            acousticness=0.2, tempo=130.0, popularity=90
        )
        cls.pop_candidate.genres.add(cls.pop)

    def test_excludes_input_tracks(self):
        """Input track IDs must not appear in candidates."""
        candidates = get_candidates_with_serendipity(
            input_genre_ids={self.rock.id},
            input_track_ids={self.input_track.id}
        )
        candidate_ids = set(candidates.values_list('id', flat=True))
        self.assertNotIn(self.input_track.id, candidate_ids)

    def test_includes_genre_matched_tracks(self):
        """Genre-matched tracks should be in the candidate set."""
        candidates = get_candidates_with_serendipity(
            input_genre_ids={self.rock.id},
            input_track_ids={self.input_track.id}
        )
        candidate_ids = set(candidates.values_list('id', flat=True))
        self.assertIn(self.rock_candidate.id, candidate_ids)

    def test_empty_genres_falls_back_to_popularity(self):
        """Empty genre set returns candidates ordered by popularity."""
        candidates = get_candidates_with_serendipity(
            input_genre_ids=set(),
            input_track_ids={self.input_track.id}
        )
        candidate_ids = set(candidates.values_list('id', flat=True))
        # Should still return tracks (popularity-based fallback)
        self.assertGreater(len(candidate_ids), 0)
        self.assertNotIn(self.input_track.id, candidate_ids)

    def test_discovery_candidates_excluded_from_genre_pool(self):
        """Discovery (serendipity) candidates come from outside input genres."""
        candidates = get_candidates_with_serendipity(
            input_genre_ids={self.rock.id},
            input_track_ids={self.input_track.id},
            serendipity_ratio=0.5
        )
        candidate_ids = set(candidates.values_list('id', flat=True))
        # Pop candidate should be included via discovery pool
        self.assertIn(self.pop_candidate.id, candidate_ids)


# ---------------------------------------------------------------------------
# Categorical preference tests (task 1.2)
# ---------------------------------------------------------------------------


class CategoricalPreferenceTestCase(TestCase):
    """Test calculate_categorical_preferences and apply_categorical_preferences."""

    @classmethod
    def setUpTestData(cls):
        """Create diverse test data with genres, countries, decades."""
        cls.rock = Genre.objects.create(name='cat_rock')
        cls.pop = Genre.objects.create(name='cat_pop')
        cls.jazz = Genre.objects.create(name='cat_jazz')

        cls.us_artist = Artist.objects.create(
            id='cat_us_artist', name='US Rock Artist', popularity=70,
            origin_country='US', artist_type='Group', formed_year=1990
        )
        cls.gb_artist = Artist.objects.create(
            id='cat_gb_artist', name='GB Pop Artist', popularity=60,
            origin_country='GB', artist_type='Person', formed_year=2005
        )
        cls.jp_artist = Artist.objects.create(
            id='cat_jp_artist', name='JP Jazz Artist', popularity=50,
            origin_country='JP', artist_type='Group', formed_year=1975
        )

        cls.rock_track = Track.objects.create(
            id='cat_rock_track', title='Rock Song', artist=cls.us_artist,
            valence=0.7, energy=0.8, danceability=0.5,
            acousticness=0.2, tempo=140.0, popularity=70
        )
        cls.rock_track.genres.add(cls.rock)

        cls.pop_track = Track.objects.create(
            id='cat_pop_track', title='Pop Song', artist=cls.gb_artist,
            valence=0.8, energy=0.6, danceability=0.9,
            acousticness=0.1, tempo=115.0, popularity=80
        )
        cls.pop_track.genres.add(cls.pop)

        cls.jazz_track = Track.objects.create(
            id='cat_jazz_track', title='Jazz Song', artist=cls.jp_artist,
            valence=0.4, energy=0.3, danceability=0.2,
            acousticness=0.8, tempo=90.0, popularity=55
        )
        cls.jazz_track.genres.add(cls.jazz)

        cls.session_key = 'cat_pref_session_001'

    def test_no_feedback_returns_no_preferences(self):
        """Session with no feedback returns has_preferences=False."""
        result = calculate_categorical_preferences('nonexistent_session')
        self.assertFalse(result['has_preferences'])

    def test_none_session_returns_no_preferences(self):
        """None session_key returns has_preferences=False."""
        result = calculate_categorical_preferences(None)
        self.assertFalse(result['has_preferences'])

    def test_liked_genres_counted(self):
        """Liked tracks have their genres counted in genres.liked."""
        RecommendationFeedback.objects.create(
            track=self.rock_track, score=True, session_key=self.session_key
        )
        result = calculate_categorical_preferences(self.session_key)
        self.assertTrue(result['has_preferences'])
        self.assertEqual(result['genres']['liked'].get('cat_rock'), 1)

    def test_disliked_genres_counted(self):
        """Disliked tracks have their genres counted in genres.disliked."""
        RecommendationFeedback.objects.create(
            track=self.pop_track, score=False, session_key=self.session_key
        )
        result = calculate_categorical_preferences(self.session_key)
        self.assertTrue(result['has_preferences'])
        self.assertEqual(result['genres']['disliked'].get('cat_pop'), 1)

    def test_country_preferences(self):
        """Country preferences are accumulated from artist origin_country."""
        RecommendationFeedback.objects.create(
            track=self.rock_track, score=True, session_key=self.session_key
        )
        RecommendationFeedback.objects.create(
            track=self.jazz_track, score=False, session_key=self.session_key
        )
        result = calculate_categorical_preferences(self.session_key)
        self.assertEqual(result['countries']['liked'].get('US'), 1)
        self.assertEqual(result['countries']['disliked'].get('JP'), 1)

    def test_decade_preferences(self):
        """Decade preferences based on artist formed_year."""
        RecommendationFeedback.objects.create(
            track=self.rock_track, score=True, session_key=self.session_key
        )
        result = calculate_categorical_preferences(self.session_key)
        self.assertEqual(result['decades']['liked'].get('1990s'), 1)

    def test_multiple_feedback_entries(self):
        """Multiple likes for same genre accumulate."""
        extra_rock_track = Track.objects.create(
            id='cat_rock_track_2', title='Rock Song 2', artist=self.us_artist,
            valence=0.6, energy=0.7, danceability=0.4,
            acousticness=0.3, tempo=130.0, popularity=65
        )
        extra_rock_track.genres.add(self.rock)

        RecommendationFeedback.objects.create(
            track=self.rock_track, score=True, session_key=self.session_key
        )
        RecommendationFeedback.objects.create(
            track=extra_rock_track, score=True, session_key=self.session_key
        )
        result = calculate_categorical_preferences(self.session_key)
        self.assertEqual(result['genres']['liked'].get('cat_rock'), 2)

    def test_apply_categorical_preferences_no_prefs(self):
        """No preferences returns original scores with empty adjustments."""
        recommendations = [(self.rock_track, 85.0), (self.pop_track, 70.0)]
        no_prefs = {'has_preferences': False}
        result = apply_categorical_preferences(recommendations, no_prefs, {})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][1], 85.0)
        self.assertEqual(len(result[0][2]), 0)

    def test_apply_categorical_preferences_boosts_liked(self):
        """Liked genre should boost recommendation score."""
        prefs = {
            'has_preferences': True,
            'genres': {'liked': {'cat_rock': 3}, 'disliked': {}},
            'countries': {'liked': {}, 'disliked': {}},
            'artist_types': {'liked': {}, 'disliked': {}},
            'decades': {'liked': {}, 'disliked': {}},
        }
        recommendations = [(self.rock_track, 80.0), (self.pop_track, 80.0)]
        result = apply_categorical_preferences(recommendations, prefs, {})
        rock_score = next(s for t, s, a in result if t.id == 'cat_rock_track')
        pop_score = next(s for t, s, a in result if t.id == 'cat_pop_track')
        self.assertGreater(rock_score, pop_score)

    def test_apply_categorical_preferences_penalizes_disliked(self):
        """Disliked genre should reduce recommendation score."""
        prefs = {
            'has_preferences': True,
            'genres': {'liked': {}, 'disliked': {'cat_pop': 2}},
            'countries': {'liked': {}, 'disliked': {}},
            'artist_types': {'liked': {}, 'disliked': {}},
            'decades': {'liked': {}, 'disliked': {}},
        }
        recommendations = [(self.rock_track, 80.0), (self.pop_track, 80.0)]
        result = apply_categorical_preferences(recommendations, prefs, {})
        rock_score = next(s for t, s, a in result if t.id == 'cat_rock_track')
        pop_score = next(s for t, s, a in result if t.id == 'cat_pop_track')
        self.assertGreater(rock_score, pop_score)


# ---------------------------------------------------------------------------
# Enhanced recommendation filter tests (task 1.3)
# ---------------------------------------------------------------------------


class EnhancedRecommendationFilterTestCase(TestCase):
    """Test get_enhanced_recommendations with country, decade, and analyzed filters."""

    @classmethod
    def setUpTestData(cls):
        """Create artists with known origin_country, formed_year, artist_type."""
        cls.genre = Genre.objects.create(name='filt_genre')

        cls.us_80s = Artist.objects.create(
            id='filt_us_80s', name='US 80s Band', popularity=70,
            origin_country='US', artist_type='Group', formed_year=1985
        )
        cls.gb_90s = Artist.objects.create(
            id='filt_gb_90s', name='GB 90s Singer', popularity=65,
            origin_country='GB', artist_type='Person', formed_year=1993
        )
        cls.jp_00s = Artist.objects.create(
            id='filt_jp_00s', name='JP 00s Band', popularity=55,
            origin_country='JP', artist_type='Group', formed_year=2002
        )

        # Input track
        cls.input_artist = Artist.objects.create(
            id='filt_input_artist', name='Filter Input Artist', popularity=60
        )
        cls.input_track = Track.objects.create(
            id='filt_input', title='Filter Input Track', artist=cls.input_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=60
        )
        cls.input_track.genres.add(cls.genre)

        cls.us_track = Track.objects.create(
            id='filt_us_track', title='US Track', artist=cls.us_80s,
            valence=0.55, energy=0.55, danceability=0.55,
            acousticness=0.45, tempo=125.0, popularity=70
        )
        cls.us_track.genres.add(cls.genre)

        cls.gb_track = Track.objects.create(
            id='filt_gb_track', title='GB Track', artist=cls.gb_90s,
            valence=0.52, energy=0.52, danceability=0.52,
            acousticness=0.48, tempo=118.0, popularity=65
        )
        cls.gb_track.genres.add(cls.genre)

        cls.jp_track = Track.objects.create(
            id='filt_jp_track', title='JP Track', artist=cls.jp_00s,
            valence=0.53, energy=0.53, danceability=0.53,
            acousticness=0.47, tempo=122.0, popularity=55
        )
        cls.jp_track.genres.add(cls.genre)

        cls.unanalyzed_track = Track.objects.create(
            id='filt_unanalyzed', title='Unanalyzed Track', artist=cls.us_80s,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
            is_audio_analyzed=False
        )
        cls.unanalyzed_track.genres.add(cls.genre)

    def test_country_filter_narrows_results(self):
        """Country filter limits results to matching country."""
        result = get_enhanced_recommendations(
            [self.input_track.id], country_filter='US', limit=10
        )
        rec_ids = [t.id for t in result['recommendations']]
        # US tracks should appear, non-US tracks should not
        for tid in rec_ids:
            track = Track.objects.get(id=tid)
            self.assertEqual(track.artist.origin_country, 'US')
        self.assertIn('country', result['filters_applied'])

    def test_decade_filter_constrains_window(self):
        """Decade filter constrains to correct 10-year window."""
        result = get_enhanced_recommendations(
            [self.input_track.id], decade_filter='1990', limit=10
        )
        rec_ids = [t.id for t in result['recommendations']]
        for tid in rec_ids:
            track = Track.objects.get(id=tid)
            self.assertGreaterEqual(track.artist.formed_year, 1990)
            self.assertLessEqual(track.artist.formed_year, 1999)
        self.assertIn('decade', result['filters_applied'])

    def test_exclude_unanalyzed_removes_unanalyzed(self):
        """exclude_unanalyzed=True removes tracks with is_audio_analyzed=False."""
        result = get_enhanced_recommendations(
            [self.input_track.id], exclude_unanalyzed=True, limit=20
        )
        rec_ids = [t.id for t in result['recommendations']]
        self.assertNotIn('filt_unanalyzed', rec_ids)
        self.assertTrue(result['filters_applied'].get('analyzed_only'))

    def test_combined_filters(self):
        """Multiple filters applied together narrow results correctly."""
        result = get_enhanced_recommendations(
            [self.input_track.id],
            country_filter='US',
            decade_filter='1980',
            limit=10
        )
        rec_ids = [t.id for t in result['recommendations']]
        for tid in rec_ids:
            track = Track.objects.get(id=tid)
            self.assertEqual(track.artist.origin_country, 'US')
            self.assertGreaterEqual(track.artist.formed_year, 1980)
            self.assertLessEqual(track.artist.formed_year, 1989)

    def test_no_filter_returns_all_candidates(self):
        """Without filters, recommendations include tracks from any country/decade."""
        result = get_enhanced_recommendations(
            [self.input_track.id], limit=20
        )
        self.assertGreater(len(result['recommendations']), 0)
        self.assertEqual(result['filters_applied'], {})


# ---------------------------------------------------------------------------
# Explanation generation tests (task 1.4)
# ---------------------------------------------------------------------------


class ExplanationGenerationTestCase(TestCase):
    """Test _generate_explanations output: reasons, similarity_score, distance thresholds."""

    @classmethod
    def setUpTestData(cls):
        """Build controlled data with known distances, genres, countries, decades."""
        cls.genre = Genre.objects.create(name='expl_rock')

        cls.input_artist = Artist.objects.create(
            id='expl_input_artist', name='Explanation Input Artist', popularity=70,
            origin_country='US', formed_year=1990
        )
        cls.input_track = Track.objects.create(
            id='expl_input', title='Explanation Input', artist=cls.input_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=100.0, popularity=70
        )
        cls.input_track.genres.add(cls.genre)

        # Very close track: distance < 0.15
        cls.close_artist = Artist.objects.create(
            id='expl_close_artist', name='Close Artist', popularity=65,
            origin_country='US', formed_year=1992
        )
        cls.close_track = Track.objects.create(
            id='expl_close', title='Close Track', artist=cls.close_artist,
            valence=0.51, energy=0.51, danceability=0.51,
            acousticness=0.49, tempo=102.0, popularity=65
        )
        cls.close_track.genres.add(cls.genre)

        # Medium distance track: 0.15-0.25
        cls.medium_artist = Artist.objects.create(
            id='expl_med_artist', name='Medium Artist', popularity=60,
            origin_country='GB', formed_year=2000
        )
        cls.medium_track = Track.objects.create(
            id='expl_medium', title='Medium Track', artist=cls.medium_artist,
            valence=0.6, energy=0.6, danceability=0.6,
            acousticness=0.4, tempo=120.0, popularity=60
        )
        cls.medium_track.genres.add(cls.genre)

        # Far distance track: 0.25-0.4
        cls.far_artist = Artist.objects.create(
            id='expl_far_artist', name='Far Artist', popularity=55,
            origin_country='JP', formed_year=1975
        )
        cls.far_track = Track.objects.create(
            id='expl_far', title='Far Track', artist=cls.far_artist,
            valence=0.7, energy=0.7, danceability=0.7,
            acousticness=0.3, tempo=140.0, popularity=55
        )
        cls.far_track.genres.add(cls.genre)

    def test_similarity_score_formula(self):
        """similarity_score = max(0, 1 - distance) * 100."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance = 0.3
        distance_map = {self.close_track.id: distance}

        explanations = _generate_explanations(
            [self.close_track], [self.input_track],
            target_vector, distance_map, {}
        )
        expected_score = round(max(0, 1 - distance) * 100, 1)
        self.assertAlmostEqual(
            explanations[self.close_track.id]['similarity_score'],
            expected_score, places=1
        )

    def test_very_close_distance_reason(self):
        """Distance < 0.15 produces 'Very similar audio profile' reason."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.close_track.id: 0.10}

        explanations = _generate_explanations(
            [self.close_track], [self.input_track],
            target_vector, distance_map, {}
        )
        reasons = explanations[self.close_track.id]['reasons']
        self.assertTrue(
            any('Very similar audio profile' in r for r in reasons)
        )

    def test_medium_distance_reason(self):
        """Distance 0.15-0.25 produces 'Similar energy and mood' reason."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.medium_track.id: 0.20}

        explanations = _generate_explanations(
            [self.medium_track], [self.input_track],
            target_vector, distance_map, {}
        )
        reasons = explanations[self.medium_track.id]['reasons']
        self.assertTrue(
            any('Similar energy and mood' in r for r in reasons)
        )

    def test_moderate_distance_reason(self):
        """Distance 0.25-0.4 produces 'Moderate audio similarity' reason."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.far_track.id: 0.30}

        explanations = _generate_explanations(
            [self.far_track], [self.input_track],
            target_vector, distance_map, {}
        )
        reasons = explanations[self.far_track.id]['reasons']
        self.assertTrue(
            any('Moderate audio similarity' in r for r in reasons)
        )

    def test_shared_genre_explanation(self):
        """Tracks sharing genres get genre-based reasons."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.close_track.id: 0.10}

        explanations = _generate_explanations(
            [self.close_track], [self.input_track],
            target_vector, distance_map, {}
        )
        reasons = explanations[self.close_track.id]['reasons']
        self.assertTrue(
            any('genre' in r.lower() for r in reasons)
        )

    def test_same_country_explanation(self):
        """Tracks from same country as input get country-based reasons."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.close_track.id: 0.10}

        explanations = _generate_explanations(
            [self.close_track], [self.input_track],
            target_vector, distance_map, {}
        )
        reasons = explanations[self.close_track.id]['reasons']
        self.assertTrue(
            any('United States' in r for r in reasons)
        )

    def test_explanation_contains_distance(self):
        """Each explanation includes the raw distance value."""
        target_vector = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        distance_map = {self.close_track.id: 0.123}

        explanations = _generate_explanations(
            [self.close_track], [self.input_track],
            target_vector, distance_map, {}
        )
        self.assertAlmostEqual(
            explanations[self.close_track.id]['distance'], 0.123, places=3
        )

    def test_full_pipeline_includes_explanations(self):
        """get_enhanced_recommendations includes explanations by default."""
        result = get_enhanced_recommendations(
            [self.input_track.id], limit=5, include_explanations=True
        )
        for track in result['recommendations']:
            self.assertIn(track.id, result['explanations'])
            expl = result['explanations'][track.id]
            self.assertIn('reasons', expl)
            self.assertIn('similarity_score', expl)
            self.assertIn('distance', expl)


# ---------------------------------------------------------------------------
# Factory-based tests (task 4.2) -- uses factories for convenience
# ---------------------------------------------------------------------------


class FactoryBasedServiceTestCase(TestCase):
    """Test services using factory_boy factories for data creation."""

    def test_recommendation_with_factory_tracks(self):
        """Factories produce valid data that works with the recommendation engine."""
        from catalog.tests.factories import (
            ArtistFactory, TrackFactory, GenreFactory
        )
        genre = GenreFactory(name='factory_test_genre')
        artist = ArtistFactory(id='factory_artist_1', name='Factory Artist')

        input_track = TrackFactory(
            id='factory_input', artist=artist, genres=[genre],
            valence=0.5, energy=0.5
        )
        candidate = TrackFactory(
            id='factory_candidate', artist=artist, genres=[genre],
            valence=0.52, energy=0.52
        )

        result = get_recommendations_from_sequence([input_track.id], limit=5)
        self.assertIn('recommendations', result)
        self.assertEqual(len(result['input_tracks']), 1)

    def test_feedback_factory(self):
        """RecommendationFeedbackFactory creates valid feedback entries."""
        from catalog.tests.factories import (
            RecommendationFeedbackFactory, TrackFactory, GenreFactory
        )
        genre = GenreFactory(name='fb_factory_genre')
        track = TrackFactory(id='fb_factory_track', genres=[genre])

        feedback = RecommendationFeedbackFactory(
            track=track, score=True, session_key='factory_session'
        )
        self.assertTrue(feedback.score)
        self.assertEqual(feedback.session_key, 'factory_session')

        result = calculate_categorical_preferences('factory_session')
        self.assertTrue(result['has_preferences'])


# ---------------------------------------------------------------------------
# Search ranking tests (task 1.7)
# ---------------------------------------------------------------------------


class SearchRankingTestCase(TestCase):
    """Test search_tracks sorts results by relevance tiers:
    exact artist > startswith artist > exact title > partial match > popularity.
    """

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name='rank_genre')

        # Artist whose name IS the query exactly
        cls.exact_artist = Artist.objects.create(
            id='rank_exact_artist', name='Aurora', popularity=60
        )
        # Artist whose name starts with the query
        cls.starts_artist = Artist.objects.create(
            id='rank_starts_artist', name='Aurora Borealis', popularity=65
        )
        # Artist unrelated to the query (used for title matches)
        cls.other_artist = Artist.objects.create(
            id='rank_other_artist', name='Other Band', popularity=90
        )
        # Artist with partial match
        cls.partial_artist = Artist.objects.create(
            id='rank_partial_artist', name='DJ Aurora Mix', popularity=70
        )

        # Track by exact-match artist (low popularity to prove sort isn't just by pop)
        cls.track_exact_artist = Track.objects.create(
            id='rank_t1', title='Runaway', artist=cls.exact_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=40,
        )
        cls.track_exact_artist.genres.add(cls.genre)

        # Track by startswith artist
        cls.track_starts_artist = Track.objects.create(
            id='rank_t2', title='Northern Lights', artist=cls.starts_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=50,
        )
        cls.track_starts_artist.genres.add(cls.genre)

        # Track whose title IS the query exactly
        cls.track_exact_title = Track.objects.create(
            id='rank_t3', title='Aurora', artist=cls.other_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=95,
        )
        cls.track_exact_title.genres.add(cls.genre)

        # Track by partial-match artist
        cls.track_partial = Track.objects.create(
            id='rank_t4', title='Sunset Mix', artist=cls.partial_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=85,
        )
        cls.track_partial.genres.add(cls.genre)

        # Track with partial title match (high popularity to test that pop alone doesn't win)
        cls.track_partial_title = Track.objects.create(
            id='rank_t5', title='The Aurora Effect', artist=cls.other_artist,
            valence=0.5, energy=0.5, danceability=0.5,
            acousticness=0.5, tempo=120.0, popularity=99,
        )
        cls.track_partial_title.genres.add(cls.genre)

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_exact_artist_ranked_first(self, mock_spotify):
        """Tracks by exact artist name match should appear first."""
        results = search_tracks('Aurora', limit=20)
        result_ids = [t.id for t in results]
        self.assertIn('rank_t1', result_ids)
        # Exact artist match should come before startswith
        idx_exact = result_ids.index('rank_t1')
        idx_starts = result_ids.index('rank_t2')
        self.assertLess(idx_exact, idx_starts)

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_startswith_before_exact_title(self, mock_spotify):
        """Startswith artist match should rank before exact title match."""
        results = search_tracks('Aurora', limit=20)
        result_ids = [t.id for t in results]
        idx_starts = result_ids.index('rank_t2')
        idx_exact_title = result_ids.index('rank_t3')
        self.assertLess(idx_starts, idx_exact_title)

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_exact_title_before_partial(self, mock_spotify):
        """Exact title match should rank before partial artist match."""
        results = search_tracks('Aurora', limit=20)
        result_ids = [t.id for t in results]
        idx_exact_title = result_ids.index('rank_t3')
        idx_partial = result_ids.index('rank_t4')
        self.assertLess(idx_exact_title, idx_partial)

    @patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[])
    def test_overall_ranking_order(self, mock_spotify):
        """Full ranking: exact artist > startswith > exact title > partial artist > partial title."""
        results = search_tracks('Aurora', limit=20)
        result_ids = [t.id for t in results]

        expected_order = ['rank_t1', 'rank_t2', 'rank_t3', 'rank_t4', 'rank_t5']
        actual_positions = [result_ids.index(tid) for tid in expected_order]
        self.assertEqual(actual_positions, sorted(actual_positions))
