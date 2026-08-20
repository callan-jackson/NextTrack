"""Client for the Deezer public API.

Deezer is the default search provider because it needs no credentials at all:
no client ID, no secret, no dashboard app, no subscription that can lapse. That
matters here - Spotify's February 2026 migration made Development Mode apps
depend on the owner holding an active Premium subscription, so search stopped
working for reasons entirely outside the codebase.

The public catalogue endpoints (``/search``, ``/track``, ``/album``,
``/artist``) are unauthenticated and return JSON. Only user-scoped operations
need OAuth, and NextTrack does not use any.

The other reason to prefer Deezer: every search result carries a ``preview``
URL pointing at a 30-second MP3. That clip is the input to
``catalog.audio_analysis``, which is how tracks get real feature vectors now
that Spotify's audio-features endpoint is gone.
"""

import logging
import threading
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 10

# Deezer allows roughly 50 requests per 5 seconds. We pace to a comfortable
# fraction of that; search is user-facing and never batches hard.
_MIN_REQUEST_INTERVAL = 0.12

# Deezer signals errors in a 200 body rather than a status code. Code 4 is the
# quota error, which we treat the way a 429 would be treated.
_QUOTA_ERROR_CODE = 4


class DeezerClientError(Exception):
    """Base exception for Deezer API errors."""


class DeezerRateLimitError(DeezerClientError):
    """Raised when Deezer reports its quota has been exceeded."""


class DeezerClient:
    """Thread-safe client for the Deezer public API."""

    API_BASE_URL = "https://api.deezer.com"

    # Deezer's own ceiling; unlike Spotify's post-migration limit of 10 this
    # comfortably covers a full page of search results.
    SEARCH_LIMIT_MAX = 50

    def __init__(self, user_agent: str = "NextTrack/1.0 (+https://github.com/callan-jackson/NextTrack)"):
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': user_agent})
        self._throttle_lock = threading.Lock()
        self._last_request = 0.0

    @property
    def is_configured(self) -> bool:
        """Always True - the public API needs no credentials.

        Kept so callers can treat providers interchangeably.
        """
        return True

    def _throttle(self):
        """Space requests out to stay inside Deezer's rate limit."""
        with self._throttle_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request = time.monotonic()

    def _make_request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Optional[dict[str, Any]]:
        """GET an endpoint with retries and backoff."""
        url = f"{self.API_BASE_URL}{endpoint}"

        for attempt in range(max_retries):
            self._throttle()

            try:
                response = self._session.get(
                    url,
                    params=params,
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                )
            except requests.RequestException as exc:
                logger.warning(f"Deezer request failed (attempt {attempt + 1}): {exc}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise DeezerClientError(f"Request failed after {max_retries} attempts: {exc}") from exc

            if response.status_code == 404:
                logger.debug(f"Deezer resource not found: {endpoint}")
                return None

            if response.status_code != 200:
                logger.error(f"Deezer API error: {response.status_code}")
                raise DeezerClientError(f"API error: {response.status_code}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise DeezerClientError(f"Malformed JSON from Deezer: {exc}") from exc

            error = payload.get('error') if isinstance(payload, dict) else None
            if error:
                code = error.get('code')
                message = error.get('message', 'unknown error')

                if code == _QUOTA_ERROR_CODE:
                    logger.warning("Deezer quota exceeded, backing off")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise DeezerRateLimitError(f"Quota exceeded: {message}")

                raise DeezerClientError(f"API error {code}: {message}")

            return payload

        raise DeezerClientError("Max retries exceeded")

    def search_tracks(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        """Search the catalogue for tracks matching a free-text query."""
        if not query or not query.strip():
            return []

        try:
            payload = self._make_request(
                '/search',
                params={
                    'q': query,
                    'limit': max(1, min(limit, self.SEARCH_LIMIT_MAX)),
                },
            )
        except DeezerClientError as exc:
            logger.error(f"Deezer search failed for '{query}': {exc}")
            return []

        if not payload or 'data' not in payload:
            return []

        tracks = [t for t in payload['data'] if t.get('type') == 'track' or 'title' in t]
        logger.info(f"Deezer search '{query}' returned {len(tracks)} tracks")
        return tracks

    def get_track(self, track_id: str) -> Optional[dict[str, Any]]:
        """Fetch full metadata for one track.

        Worth calling when a stored preview URL has expired: Deezer signs
        preview links with a timestamp, so a fresh one must be fetched rather
        than reused indefinitely.
        """
        try:
            return self._make_request(f'/track/{track_id}')
        except DeezerClientError as exc:
            logger.error(f"Failed to get Deezer track {track_id}: {exc}")
            return None

    def get_album(self, album_id: str) -> Optional[dict[str, Any]]:
        """Fetch album metadata, which is where Deezer exposes genres."""
        try:
            return self._make_request(f'/album/{album_id}')
        except DeezerClientError as exc:
            logger.error(f"Failed to get Deezer album {album_id}: {exc}")
            return None

    def get_artist(self, artist_id: str) -> Optional[dict[str, Any]]:
        """Fetch artist metadata (fan counts, picture, name)."""
        try:
            return self._make_request(f'/artist/{artist_id}')
        except DeezerClientError as exc:
            logger.error(f"Failed to get Deezer artist {artist_id}: {exc}")
            return None


_client: Optional[DeezerClient] = None
_client_lock = threading.Lock()


def get_deezer_client() -> DeezerClient:
    """Get the module-level DeezerClient, creating it on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = DeezerClient()
    return _client
