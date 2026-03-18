"""Unit tests for catalog models (Genre, Artist, Track, UserSurvey)."""

from django.test import TestCase

from catalog.models import Genre, Artist, Track, UserSurvey


# ---------------------------------------------------------------------------
# Existing tests migrated from catalog/tests.py
# ---------------------------------------------------------------------------


class FormValidationTestCase(TestCase):
    """Test form validation logic."""

    def test_preference_form_valid_values(self):
        """Valid preference values pass validation."""
        from catalog.forms import PreferenceForm
        form = PreferenceForm(data={
            'energy': 0.7,
            'valence': 0.5,
            'danceability': 0.8,
        })
        self.assertTrue(form.is_valid())
        prefs = form.get_preferences()
        self.assertEqual(prefs['energy'], 0.7)

    def test_preference_form_out_of_range(self):
        """Out-of-range values fail validation."""
        from catalog.forms import PreferenceForm
        form = PreferenceForm(data={
            'energy': 1.5,
        })
        self.assertFalse(form.is_valid())

    def test_search_form_valid_query(self):
        """Valid search query passes validation."""
        from catalog.forms import SearchForm
        form = SearchForm(data={'query': 'test search'})
        self.assertTrue(form.is_valid())

    def test_search_form_short_query(self):
        """Too-short query fails validation."""
        from catalog.forms import SearchForm
        form = SearchForm(data={'query': 'a'})
        self.assertFalse(form.is_valid())

    def test_search_form_whitespace_normalization(self):
        """Whitespace is normalized in search queries."""
        from catalog.forms import SearchForm
        form = SearchForm(data={'query': '  multiple   spaces  '})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['query'], 'multiple spaces')


# ---------------------------------------------------------------------------
# New model property tests (task 1.8 / 1.10)
# ---------------------------------------------------------------------------


class ArtistIsEnrichedTestCase(TestCase):
    """Test Artist.is_enriched property."""

    def test_not_enriched_by_default(self):
        """Artist without MusicBrainz or Wikidata IDs is not enriched."""
        artist = Artist(id='a1', name='Plain Artist', popularity=50)
        self.assertFalse(artist.is_enriched)

    def test_enriched_with_musicbrainz_id(self):
        """Artist with musicbrainz_id is considered enriched."""
        artist = Artist(
            id='a2', name='MB Artist', popularity=50,
            musicbrainz_id='12345678-1234-1234-1234-123456789abc'
        )
        self.assertTrue(artist.is_enriched)

    def test_enriched_with_wikidata_id(self):
        """Artist with wikidata_id is considered enriched."""
        artist = Artist(
            id='a3', name='WD Artist', popularity=50,
            wikidata_id='Q12345'
        )
        self.assertTrue(artist.is_enriched)

    def test_enriched_with_both_ids(self):
        """Artist with both IDs is also enriched."""
        artist = Artist(
            id='a4', name='Both Artist', popularity=50,
            musicbrainz_id='12345678-1234-1234-1234-123456789abc',
            wikidata_id='Q12345'
        )
        self.assertTrue(artist.is_enriched)


class ArtistDecadeTestCase(TestCase):
    """Test Artist.decade property."""

    def test_decade_1987(self):
        """1987 should return 1980."""
        artist = Artist(id='d1', name='80s Band', formed_year=1987)
        self.assertEqual(artist.decade, 1980)

    def test_decade_2000(self):
        """2000 should return 2000."""
        artist = Artist(id='d2', name='Y2K Band', formed_year=2000)
        self.assertEqual(artist.decade, 2000)

    def test_decade_1969(self):
        """1969 should return 1960."""
        artist = Artist(id='d3', name='60s Band', formed_year=1969)
        self.assertEqual(artist.decade, 1960)

    def test_decade_none_when_no_formed_year(self):
        """None formed_year returns None decade."""
        artist = Artist(id='d4', name='Unknown Era Band')
        self.assertIsNone(artist.decade)


class UserSurveyAverageScoreTestCase(TestCase):
    """Test UserSurvey.average_score property."""

    def test_average_score_all_fives(self):
        """All ratings 5 should average to 5.0."""
        survey = UserSurvey(
            overall_satisfaction=5,
            discovery_rating=5,
            accuracy_rating=5
        )
        self.assertAlmostEqual(survey.average_score, 5.0)

    def test_average_score_all_ones(self):
        """All ratings 1 should average to 1.0."""
        survey = UserSurvey(
            overall_satisfaction=1,
            discovery_rating=1,
            accuracy_rating=1
        )
        self.assertAlmostEqual(survey.average_score, 1.0)

    def test_average_score_mixed(self):
        """Mixed ratings 3,4,5 should average to 4.0."""
        survey = UserSurvey(
            overall_satisfaction=3,
            discovery_rating=4,
            accuracy_rating=5
        )
        self.assertAlmostEqual(survey.average_score, 4.0)

    def test_average_score_non_integer_result(self):
        """Ratings 1,2,3 should average to 2.0."""
        survey = UserSurvey(
            overall_satisfaction=1,
            discovery_rating=2,
            accuracy_rating=3
        )
        self.assertAlmostEqual(survey.average_score, 2.0)
