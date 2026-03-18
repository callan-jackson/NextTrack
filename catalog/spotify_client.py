"""Thread-safe Spotify API client using Client Credentials auth."""

import logging
import time
import threading
from datetime import datetime, timedelta

import requests
from django.conf import settings

_CONNECT_TIMEOUT = getattr(settings, 'EXTERNAL_API_CONNECT_TIMEOUT', 5)
_READ_TIMEOUT = getattr(settings, 'EXTERNAL_API_READ_TIMEOUT', 15)

logger = logging.getLogger(__name__)


class SpotifyClientError(Exception):
    """Base exception for Spotify API errors."""
    pass


class SpotifyAuthError(SpotifyClientError):
    """Raised when OAuth authentication fails."""
    pass


class SpotifyRateLimitError(SpotifyClientError):
    """Raised when API rate limit is exceeded."""
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after} seconds.")


class SpotifyClient:
    """Thread-safe client for Spotify Web API."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE_URL = "https://api.spotify.com/v1"

    def __init__(self):
        """Set up credentials and token state."""
        self._token = None
        self._token_expires = None
        self._token_lock = threading.Lock()

        self._client_id = getattr(settings, 'SPOTIFY_CLIENT_ID', None)
        self._client_secret = getattr(settings, 'SPOTIFY_CLIENT_SECRET', None)

        if not self._client_id or not self._client_secret:
            logger.warning(
                "Spotify credentials not configured. "
                "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in settings.py"
            )

        logger.info("SpotifyClient initialized")

    @property
    def is_configured(self):
        """Check if API credentials are set."""
        return bool(self._client_id and self._client_secret)

    def _get_token(self):
        """Get a valid access token, refreshing if needed."""
        with self._token_lock:
            if self._token and self._token_expires:
                if datetime.now() < self._token_expires - timedelta(seconds=60):
                    return self._token

            if not self.is_configured:
                raise SpotifyAuthError(
                    "Spotify credentials not configured. "
                    "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in settings.py"
                )

            logger.info("Refreshing Spotify access token...")

            try:
                response = requests.post(
                    self.TOKEN_URL,
                    data={'grant_type': 'client_credentials'},
                    auth=(self._client_id, self._client_secret),
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT)
                )

                if response.status_code != 200:
                    logger.error(f"Spotify auth failed: {response.status_code} - {response.text}")
                    raise SpotifyAuthError(f"Authentication failed: {response.status_code}")

                data = response.json()
                self._token = data['access_token']
                expires_in = data.get('expires_in', 3600)
                self._token_expires = datetime.now() + timedelta(seconds=expires_in)

                logger.info(f"Spotify token refreshed, expires in {expires_in}s")
                return self._token

            except requests.RequestException as e:
                logger.error(f"Spotify auth request failed: {e}")
                raise SpotifyAuthError(f"Network error during authentication: {e}")

    def _make_request(self, method, endpoint, params=None, max_retries=3):
        """Make an API request with retries and token refresh."""
        url = f"{self.API_BASE_URL}{endpoint}"

        for attempt in range(max_retries):
            try:
                token = self._get_token()
                headers = {
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json'
                }

                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT)
                )

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 5))
                    logger.warning(f"Spotify rate limited. Waiting {retry_after}s...")

                    if attempt < max_retries - 1:
                        time.sleep(retry_after)
                        continue
                    else:
                        raise SpotifyRateLimitError(retry_after)

                if response.status_code == 401:
                    logger.warning("Spotify token expired, refreshing...")
                    with self._token_lock:
                        self._token = None
                        self._token_expires = None
                    continue

                if response.status_code == 404:
                    logger.debug(f"Spotify resource not found: {endpoint}")
                    return None

                logger.error(f"Spotify API error: {response.status_code} - {response.text}")
                raise SpotifyClientError(f"API error: {response.status_code}")

            except requests.RequestException as e:
                logger.error(f"Spotify request failed (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                else:
                    raise SpotifyClientError(f"Request failed after {max_retries} attempts: {e}")

        raise SpotifyClientError("Max retries exceeded")

    def search_tracks(self, query, limit=20):
        """Search Spotify for tracks by query string."""
        if not self.is_configured:
            logger.warning("Spotify not configured, skipping search")
            return []

        try:
            response = self._make_request(
                'GET',
                '/search',
                params={
                    'q': query,
                    'type': 'track',
                    'limit': min(limit, 50)
                }
            )

            if not response or 'tracks' not in response:
                return []

            tracks = response['tracks'].get('items', [])
            logger.info(f"Spotify search '{query}' returned {len(tracks)} tracks")

            return tracks

        except SpotifyClientError as e:
            logger.error(f"Spotify search failed: {e}")
            return []

    def get_audio_features(self, track_id):
        """Get audio features (danceability, energy, etc.) for a track."""
        if not self.is_configured:
            logger.warning("Spotify not configured, skipping audio features fetch")
            return None

        try:
            response = self._make_request(
                'GET',
                f'/audio-features/{track_id}'
            )

            if response:
                logger.debug(f"Got audio features for track {track_id}")

            return response

        except SpotifyClientError as e:
            logger.error(f"Failed to get audio features for {track_id}: {e}")
            return None

    def get_audio_features_batch(self, track_ids):
        """Get audio features for multiple tracks at once (max 100)."""
        if not self.is_configured:
            return {}

        if not track_ids:
            return {}

        track_ids = track_ids[:100]

        try:
            response = self._make_request(
                'GET',
                '/audio-features',
                params={'ids': ','.join(track_ids)}
            )

            if not response or 'audio_features' not in response:
                return {}

            features_map = {}
            for features in response['audio_features']:
                if features:
                    features_map[features['id']] = features

            logger.info(f"Got audio features for {len(features_map)}/{len(track_ids)} tracks")
            return features_map

        except SpotifyClientError as e:
            logger.error(f"Batch audio features failed: {e}")
            return {}

    def get_track(self, track_id):
        """Get metadata for a single track."""
        if not self.is_configured:
            return None

        try:
            return self._make_request('GET', f'/tracks/{track_id}')
        except SpotifyClientError as e:
            logger.error(f"Failed to get track {track_id}: {e}")
            return None

    def get_recommendations(self, seed_track_ids, limit=20):
        """Get track recommendations based on seed tracks."""
        if not self.is_configured:
            return []

        if not seed_track_ids:
            return []

        seeds = seed_track_ids[:5]

        try:
            response = self._make_request(
                'GET',
                '/recommendations',
                params={
                    'seed_tracks': ','.join(seeds),
                    'limit': min(limit, 100)
                }
            )

            if not response or 'tracks' not in response:
                return []

            tracks = response['tracks']
            logger.info(f"Got {len(tracks)} recommendations from Spotify")
            return tracks

        except SpotifyClientError as e:
            logger.error(f"Failed to get recommendations: {e}")
            return []

    def get_artist(self, artist_id):
        """Get artist metadata including genres."""
        if not self.is_configured:
            return None

        try:
            return self._make_request('GET', f'/artists/{artist_id}')
        except SpotifyClientError as e:
            logger.error(f"Failed to get artist {artist_id}: {e}")
            return None


_client = None


def get_spotify_client():
    """Get or create the module-level SpotifyClient instance."""
    global _client
    if _client is None:
        _client = SpotifyClient()
    return _client
