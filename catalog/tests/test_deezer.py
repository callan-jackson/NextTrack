"""Tests for the Deezer search provider and its ingest path.

Everything here is offline: the HTTP layer is mocked so the suite never depends
on Deezer being reachable.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from catalog.deezer_client import DeezerClient, DeezerClientError, DeezerRateLimitError
from catalog.models import Artist, Track
from catalog.services import (
    _deezer_rank_to_popularity,
    _fetch_and_ingest_from_deezer,
    _fetch_and_ingest_from_provider,
    get_search_provider_name,
    ingest_track_from_deezer_data,
)


def deezer_track(track_id=138547415, title='Creep', artist='Radiohead', rank=978547):
    """A payload shaped like a real Deezer /search item."""
    return {
        'id': track_id,
        'title': title,
        'title_short': title,
        'isrc': 'GBAYE9200070',
        'duration': 238,
        'rank': rank,
        'preview': 'https://cdnt-preview.dzcdn.net/api/1/1/b/9/c/0/example.mp3?hdnea=exp=123',
        'artist': {'id': 399, 'name': artist},
        'album': {'id': 14880711, 'title': 'Pablo Honey'},
        'type': 'track',
    }


def json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


class DeezerClientTestCase(TestCase):
    """Transport behaviour: limits, error bodies, missing resources."""

    def setUp(self):
        self.client = DeezerClient()
        # Requests are paced against Deezer's rate limit; no need to wait here.
        self.client._throttle = lambda: None

    def test_needs_no_credentials(self):
        self.assertTrue(self.client.is_configured)

    def test_search_returns_tracks(self):
        with patch.object(self.client._session, 'get',
                          return_value=json_response({'data': [deezer_track()]})):
            results = self.client.search_tracks('radiohead')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Creep')

    def test_search_clamps_limit_to_provider_maximum(self):
        with patch.object(self.client._session, 'get',
                          return_value=json_response({'data': []})) as mock_get:
            self.client.search_tracks('radiohead', limit=999)

        self.assertEqual(mock_get.call_args.kwargs['params']['limit'],
                         DeezerClient.SEARCH_LIMIT_MAX)

    def test_search_limit_never_below_one(self):
        with patch.object(self.client._session, 'get',
                          return_value=json_response({'data': []})) as mock_get:
            self.client.search_tracks('radiohead', limit=0)

        self.assertEqual(mock_get.call_args.kwargs['params']['limit'], 1)

    def test_blank_query_short_circuits(self):
        with patch.object(self.client._session, 'get') as mock_get:
            self.assertEqual(self.client.search_tracks('   '), [])
        mock_get.assert_not_called()

    def test_error_body_in_200_response_is_raised(self):
        """Deezer reports failures in the body, not the status code."""
        payload = {'error': {'type': 'Exception', 'message': 'bad request', 'code': 800}}
        with patch.object(self.client._session, 'get', return_value=json_response(payload)):
            with self.assertRaises(DeezerClientError):
                self.client._make_request('/track/1')

    def test_quota_error_raises_rate_limit_after_retries(self):
        payload = {'error': {'type': 'Exception', 'message': 'Quota limit exceeded', 'code': 4}}
        with patch.object(self.client._session, 'get', return_value=json_response(payload)):
            with patch('catalog.deezer_client.time.sleep'):
                with self.assertRaises(DeezerRateLimitError):
                    self.client._make_request('/search', params={'q': 'x'})

    def test_search_swallows_client_errors(self):
        """A provider outage yields no results rather than breaking search."""
        with patch.object(self.client, '_make_request', side_effect=DeezerClientError('boom')):
            self.assertEqual(self.client.search_tracks('radiohead'), [])

    def test_404_returns_none(self):
        with patch.object(self.client._session, 'get', return_value=json_response({}, 404)):
            self.assertIsNone(self.client._make_request('/track/0'))


class DeezerRankMappingTestCase(TestCase):
    """Deezer rank (0-1,000,000) maps onto the 0-100 popularity scale."""

    def test_maps_to_percentage_scale(self):
        self.assertEqual(_deezer_rank_to_popularity(978547), 98)
        self.assertEqual(_deezer_rank_to_popularity(500000), 50)
        self.assertEqual(_deezer_rank_to_popularity(0), 0)

    def test_clamps_above_scale(self):
        self.assertEqual(_deezer_rank_to_popularity(50_000_000), 100)

    def test_handles_missing_and_junk(self):
        self.assertEqual(_deezer_rank_to_popularity(None), 0)
        self.assertEqual(_deezer_rank_to_popularity('nonsense'), 0)


class DeezerIngestTestCase(TestCase):
    """Turning a Deezer payload into a Track row."""

    def test_creates_track_with_prefixed_id(self):
        track = ingest_track_from_deezer_data(deezer_track())

        self.assertIsNotNone(track)
        self.assertEqual(track.id, 'dz-138547415')
        self.assertEqual(track.title, 'Creep')
        self.assertEqual(track.artist.name, 'Radiohead')
        self.assertEqual(track.source, 'deezer')
        self.assertEqual(track.isrc, 'GBAYE9200070')
        self.assertEqual(track.popularity, 98)

    def test_stores_preview_url_for_later_analysis(self):
        track = ingest_track_from_deezer_data(deezer_track())
        self.assertIn('dzcdn.net', track.preview_url)

    def test_marked_unanalysed_with_neutral_features(self):
        """Features are placeholders until the audio analyser has run."""
        track = ingest_track_from_deezer_data(deezer_track())

        self.assertFalse(track.is_audio_analyzed)
        self.assertEqual(track.analysis_version, 0)
        self.assertEqual(track.valence, 0.5)
        self.assertEqual(track.energy, 0.5)
        self.assertTrue(track.needs_audio_analysis)

    def test_reuses_existing_artist(self):
        Artist.objects.create(id='radiohead', name='Radiohead', popularity=90)
        track = ingest_track_from_deezer_data(deezer_track())

        self.assertEqual(track.artist.id, 'radiohead')
        self.assertEqual(Artist.objects.filter(name='Radiohead').count(), 1)

    def test_release_year_parsed_when_present(self):
        payload = deezer_track()
        payload['release_date'] = '1993-02-22'
        self.assertEqual(ingest_track_from_deezer_data(payload).release_year, 1993)

    def test_missing_release_date_is_tolerated(self):
        self.assertIsNone(ingest_track_from_deezer_data(deezer_track()).release_year)

    def test_malformed_payload_returns_none(self):
        self.assertIsNone(ingest_track_from_deezer_data({'no': 'id'}))


@override_settings(AUDIO_ANALYSIS_ON_INGEST=False)
class DeezerSearchIngestTestCase(TestCase):
    """The search-path wrapper: dedup, and never raising at the caller."""

    def _patch_search(self, results):
        client = MagicMock()
        client.search_tracks.return_value = results
        return patch('catalog.deezer_client.get_deezer_client', return_value=client)

    def test_ingests_new_tracks(self):
        with self._patch_search([deezer_track()]):
            ingested = _fetch_and_ingest_from_deezer('radiohead')

        self.assertEqual(len(ingested), 1)
        self.assertEqual(Track.objects.count(), 1)

    def test_skips_tracks_already_stored_by_id(self):
        with self._patch_search([deezer_track()]):
            _fetch_and_ingest_from_deezer('radiohead')
            again = _fetch_and_ingest_from_deezer('radiohead')

        self.assertEqual(again, [])
        self.assertEqual(Track.objects.count(), 1)

    def test_skips_titles_the_caller_already_found_locally(self):
        keys = {('creep', 'radiohead')}
        with self._patch_search([deezer_track()]):
            ingested = _fetch_and_ingest_from_deezer('radiohead', existing_keys=keys)

        self.assertEqual(ingested, [])

    def test_does_not_duplicate_a_track_already_in_the_database(self):
        """A different provider ID for the same recording must not create a row.

        The caller only passes keys for the local results it happened to see,
        which is capped, so the ingest path re-checks the whole table.
        """
        artist = Artist.objects.create(id='radiohead', name='Radiohead')
        Track.objects.create(id='legacy-creep', title='Creep', artist=artist)

        with self._patch_search([deezer_track(track_id=999)]):
            ingested = _fetch_and_ingest_from_deezer('radiohead')

        self.assertEqual(ingested, [])
        self.assertEqual(Track.objects.count(), 1)

    def test_empty_provider_response(self):
        with self._patch_search([]):
            self.assertEqual(_fetch_and_ingest_from_deezer('nothing'), [])

    def test_provider_failure_returns_empty_not_exception(self):
        client = MagicMock()
        client.search_tracks.side_effect = DeezerClientError('down')
        with patch('catalog.deezer_client.get_deezer_client', return_value=client):
            self.assertEqual(_fetch_and_ingest_from_deezer('radiohead'), [])


class ProviderDispatchTestCase(TestCase):
    """MUSIC_SEARCH_PROVIDER selects the backend."""

    @override_settings(MUSIC_SEARCH_PROVIDER='deezer')
    def test_default_provider_is_deezer(self):
        self.assertEqual(get_search_provider_name(), 'deezer')

        with patch('catalog.services._fetch_and_ingest_from_deezer', return_value=[]) as mock:
            _fetch_and_ingest_from_provider('x')
        mock.assert_called_once()

    @override_settings(MUSIC_SEARCH_PROVIDER='spotify')
    def test_spotify_can_still_be_selected(self):
        with patch('catalog.services._fetch_and_ingest_from_spotify', return_value=[]) as mock:
            _fetch_and_ingest_from_provider('x')
        mock.assert_called_once()

    @override_settings(MUSIC_SEARCH_PROVIDER='napster')
    def test_unknown_provider_falls_back_to_deezer(self):
        with patch('catalog.services._fetch_and_ingest_from_deezer', return_value=[]) as mock:
            _fetch_and_ingest_from_provider('x')
        mock.assert_called_once()
