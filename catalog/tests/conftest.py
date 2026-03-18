"""Shared pytest fixtures for catalog tests."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_spotify_client():
    """Mock SpotifyClient returning representative response data."""
    client = MagicMock()
    client.is_configured = True

    client.search_tracks.return_value = [
        {
            'id': 'sp_track_1',
            'name': 'Mocked Track',
            'artists': [{'id': 'sp_artist_1', 'name': 'Mocked Artist'}],
            'popularity': 75,
        }
    ]

    client.get_audio_features.return_value = {
        'id': 'sp_track_1',
        'danceability': 0.7,
        'energy': 0.8,
        'valence': 0.6,
        'acousticness': 0.2,
        'tempo': 128.0,
        'loudness': -5.5,
    }

    client.get_audio_features_batch.return_value = {
        'sp_track_1': {
            'id': 'sp_track_1',
            'danceability': 0.7,
            'energy': 0.8,
            'valence': 0.6,
            'acousticness': 0.2,
            'tempo': 128.0,
            'loudness': -5.5,
        }
    }

    client.get_artist.return_value = {
        'id': 'sp_artist_1',
        'name': 'Mocked Artist',
        'genres': ['pop', 'electronic'],
        'popularity': 80,
    }

    client.get_track.return_value = {
        'id': 'sp_track_1',
        'name': 'Mocked Track',
        'artists': [{'id': 'sp_artist_1', 'name': 'Mocked Artist'}],
        'popularity': 75,
    }

    client.get_recommendations.return_value = [
        {
            'id': 'sp_rec_1',
            'name': 'Recommended Track',
            'artists': [{'id': 'sp_artist_2', 'name': 'Rec Artist'}],
            'popularity': 60,
        }
    ]

    return client


@pytest.fixture
def mock_musicbrainz_client():
    """Mock MusicBrainzClient returning representative response data."""
    client = MagicMock()

    client.search_artist.return_value = [
        {
            'id': 'mb-uuid-1234',
            'name': 'Mocked Artist',
            'country': 'US',
            'type': 'Group',
            'score': 100,
            'disambiguation': '',
            'life_span': {'begin': '1990', 'end': None},
        }
    ]

    client.get_artist_details.return_value = {
        'id': 'mb-uuid-1234',
        'name': 'Mocked Artist',
        'type': 'Group',
        'country': 'US',
        'formed_year': 1990,
        'disbanded_year': None,
        'tags': [
            {'name': 'rock', 'count': 10},
            {'name': 'alternative', 'count': 5},
        ],
        'genres': ['rock', 'alternative rock'],
        'related_artists': [],
        'wikidata_id': 'Q12345',
        'wikipedia_url': 'https://en.wikipedia.org/wiki/Mocked_Artist',
        'disambiguation': '',
    }

    client.get_artist_tags.return_value = ['rock', 'alternative']

    return client


@pytest.fixture
def mock_wikidata_client():
    """Mock WikidataClient returning representative response data."""
    client = MagicMock()

    client.get_entity.return_value = {
        'id': 'Q12345',
        'label': 'Mocked Artist',
        'description': 'American rock band',
        'formed_year': 1990,
        'genre_ids': ['Q11399', 'Q484641'],
        'country_id': 'Q30',
        'influenced_by_ids': ['Q1299', 'Q5582'],
    }

    client.get_entity_labels.return_value = {
        'Q1299': 'The Beatles',
        'Q5582': 'Led Zeppelin',
        'Q11399': 'rock music',
        'Q484641': 'alternative rock',
    }

    client.search_artist.return_value = [
        {
            'id': 'Q12345',
            'label': 'Mocked Artist',
            'description': 'American rock band',
        }
    ]

    client.get_artist_by_musicbrainz_id.return_value = {
        'id': 'Q12345',
        'label': 'Mocked Artist',
        'description': 'American rock band',
        'formed_year': 1990,
        'genre_ids': ['Q11399'],
        'country_id': 'Q30',
        'influenced_by_ids': [],
    }

    return client


@pytest.fixture
def mock_lastfm_client():
    """Mock LastFmClient returning representative response data."""
    client = MagicMock()
    client.is_available = True

    client.get_similar_artists.return_value = [
        {'name': 'Similar Band 1', 'match': 0.95, 'url': 'https://last.fm/similar1'},
        {'name': 'Similar Band 2', 'match': 0.85, 'url': 'https://last.fm/similar2'},
    ]

    client.get_artist_tags.return_value = [
        {'name': 'rock', 'count': 100},
        {'name': 'alternative', 'count': 80},
        {'name': 'indie', 'count': 60},
    ]

    client.get_artist_info.return_value = {
        'name': 'Mocked Artist',
        'listeners': 500000,
        'playcount': 12000000,
        'bio': 'An influential rock band formed in 1990.',
        'tags': ['rock', 'alternative', 'indie'],
        'similar_artists': ['Similar Band 1', 'Similar Band 2'],
        'url': 'https://last.fm/artist/mocked',
    }

    return client
