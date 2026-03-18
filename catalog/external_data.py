"""API clients for fetching artist data from MusicBrainz, Wikidata, Last.fm, and Genius.

Clients and data provided
-------------------------
* **MusicBrainzClient** -- Artist metadata (country, type, formed year, tags,
  genres, related artists, Wikidata cross-reference).
* **WikidataClient** -- Descriptions, influence relationships (P737), formation
  dates, and genre/country entity IDs.  Can also resolve a MusicBrainz MBID to
  a Wikidata Q-ID via SPARQL.
* **LastFmClient** -- Similar artists, community tags, listener/play counts,
  and artist bios.
* **GeniusClient** -- Song search, artist profile URLs, descriptions, and
  social links.
* **LiveExternalDataService** -- Orchestrates all four clients with Redis
  caching and batch-fetch support.

Required credentials (Django settings)
--------------------------------------
* ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET`` -- used by the separate
  ``spotify_client`` module, not directly here.
* ``LASTFM_API_KEY`` -- Last.fm API key.  If empty, Last.fm features are
  silently disabled.
* ``GENIUS_ACCESS_TOKEN`` -- Genius OAuth token.  If empty, Genius features are
  silently disabled.
* MusicBrainz and Wikidata are public APIs and require no credentials; only a
  User-Agent header is sent.

Rate limits
-----------
* MusicBrainz: **1 request / second** (enforced by ``_min_request_interval``).
* Last.fm: **5 requests / second** (not enforced client-side; kept under limit
  by ``batch_get_artist_info``'s ``max_live_fetches`` cap).
* Wikidata: no hard limit but requests use ``resilient_get`` with exponential
  backoff.
* Genius: rate limits vary; handled by ``resilient_get`` retries.

Caching strategy
----------------
``LiveExternalDataService`` caches per-artist results in Redis with a 24-hour
TTL (``CACHE_TTL = 86400``).  Cache keys follow the pattern
``ext_artist:v{CACHE_VERSION}:{normalised_name}``.  Incrementing
``CACHE_VERSION`` invalidates all previous entries.  If Redis is unavailable,
lookups proceed without caching and a warning is logged.

Fallback behaviour
------------------
All clients swallow connection/timeout errors and return ``None`` or empty
lists, so the recommendation engine degrades gracefully when external APIs are
down.  ``resilient_get`` retries up to 3 times with exponential back-off for
5xx and connection errors.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, TypedDict

import requests
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDict definitions for structured return values
# ---------------------------------------------------------------------------


class MusicBrainzSearchResult(TypedDict, total=False):
    id: str
    name: str
    country: Optional[str]
    type: Optional[str]
    score: int
    disambiguation: str
    life_span: dict[str, Any]


class MusicBrainzArtistDetails(TypedDict, total=False):
    id: str
    name: str
    type: Optional[str]
    country: Optional[str]
    formed_year: Optional[int]
    disbanded_year: Optional[int]
    tags: list[dict[str, Any]]
    genres: list[str]
    related_artists: list[dict[str, str]]
    wikidata_id: Optional[str]
    wikipedia_url: Optional[str]
    disambiguation: str


class WikidataEntity(TypedDict, total=False):
    id: str
    label: str
    description: str
    formed_year: Optional[int]
    genre_ids: list[str]
    country_id: Optional[str]
    influenced_by_ids: list[str]


class LastFmArtistInfo(TypedDict, total=False):
    name: str
    listeners: int
    playcount: int
    bio: Optional[str]
    tags: list[str]
    similar_artists: list[str]
    url: str


class GeniusSongResult(TypedDict, total=False):
    title: str
    artist: str
    url: str
    thumbnail: str
    genius_id: Optional[int]


class GeniusArtistInfo(TypedDict, total=False):
    name: str
    url: str
    image_url: str
    description: Optional[str]
    instagram: Optional[str]
    twitter: Optional[str]
    followers_count: int


class ArtistExternalData(TypedDict, total=False):
    name: str
    country: Optional[str]
    type: Optional[str]
    formed_year: Optional[int]
    tags: list[str]
    genres: list[str]
    description: Optional[str]
    similar_artists: list[str]
    influenced_by: list[str]
    lastfm_tags: list[str]
    listeners: int
    genius_url: Optional[str]
    data_sources: list[str]
    source: str


class EnrichmentResult(TypedDict, total=False):
    name: str
    musicbrainz: Optional[MusicBrainzArtistDetails]
    wikidata: Optional[WikidataEntity]
    combined: dict[str, Any]

_CONNECT_TIMEOUT = getattr(django_settings, 'EXTERNAL_API_CONNECT_TIMEOUT', 5)
_READ_TIMEOUT = getattr(django_settings, 'EXTERNAL_API_READ_TIMEOUT', 15)


class ExternalDataError(Exception):
    """Generic error for external API calls."""
    pass


class RateLimitError(ExternalDataError):
    """Raised when we hit an API's rate limit."""
    pass


def resilient_get(
    url: str,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: Optional[tuple[int, int]] = None,
    max_retries: int = 3,
) -> requests.Response:
    """Make a GET request with exponential backoff, retrying on 5xx and connection errors.

    Returns the requests.Response on success. Raises ExternalDataError after
    exhausting retries.
    """
    if timeout is None:
        timeout = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

    last_exception = None
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )

            if response.status_code < 500:
                return response

            logger.warning(
                f"Server error {response.status_code} from {url} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            last_exception = ExternalDataError(
                f"Server error {response.status_code} from {url}"
            )

        except requests.ConnectionError as e:
            logger.warning(
                f"Connection error for {url} (attempt {attempt + 1}/{max_retries}): {e}"
            )
            last_exception = e
        except requests.Timeout as e:
            logger.warning(
                f"Timeout for {url} (attempt {attempt + 1}/{max_retries}): {e}"
            )
            last_exception = e
        except requests.RequestException as e:
            raise ExternalDataError(f"Request failed: {e}")

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            time.sleep(wait_time)

    raise ExternalDataError(
        f"Request to {url} failed after {max_retries} attempts: {last_exception}"
    )


class MusicBrainzClient:
    """Client for MusicBrainz API with rate limiting."""

    BASE_URL = "https://musicbrainz.org/ws/2"
    USER_AGENT = "NextTrack/1.0 (University Project - Music Recommendation API)"

    def __init__(self):
        self._last_request_time = 0
        self._min_request_interval = 1.1  # Slightly over 1 second for safety margin

    def _rate_limit(self):
        """Sleep if needed to stay under 1 req/sec."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _make_request(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Make a rate-limited request to MusicBrainz."""
        self._rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json'
        }

        if params is None:
            params = {}
        params['fmt'] = 'json'

        try:
            response = resilient_get(url, params=params, headers=headers)

            if response.status_code == 503:
                logger.warning("MusicBrainz rate limited")
                raise RateLimitError("MusicBrainz rate limited")

            if response.status_code == 404:
                return None

            response.raise_for_status()
            return response.json()

        except ExternalDataError:
            raise
        except requests.RequestException as e:
            logger.error(f"MusicBrainz request failed: {e}")
            raise ExternalDataError(f"MusicBrainz request failed: {e}")

    def search_artist(self, name: str, limit: int = 5) -> list[MusicBrainzSearchResult]:
        """Search for an artist by name."""
        try:
            data = self._make_request('artist', {
                'query': f'artist:"{name}"',
                'limit': limit
            })

            if not data or 'artists' not in data:
                return []

            results = []
            for artist in data['artists']:
                results.append({
                    'id': artist.get('id'),
                    'name': artist.get('name'),
                    'country': artist.get('country'),
                    'type': artist.get('type'),
                    'score': artist.get('score', 0),
                    'disambiguation': artist.get('disambiguation', ''),
                    'life_span': artist.get('life-span', {})
                })

            return results

        except ExternalDataError:
            return []

    def get_artist_details(self, mbid: str) -> Optional[MusicBrainzArtistDetails]:
        """Get detailed artist info by MusicBrainz ID."""
        try:
            data = self._make_request(f'artist/{mbid}', {
                'inc': 'tags+genres+url-rels+artist-rels'
            })

            if not data:
                return None

            life_span = data.get('life-span', {})
            begin = life_span.get('begin', '')
            end = life_span.get('end', '')

            formed_year = None
            disbanded_year = None
            if begin:
                try:
                    formed_year = int(begin[:4])
                except (ValueError, IndexError):
                    pass
            if end:
                try:
                    disbanded_year = int(end[:4])
                except (ValueError, IndexError):
                    pass

            tags = []
            for tag in data.get('tags', []):
                tags.append({
                    'name': tag.get('name'),
                    'count': tag.get('count', 0)
                })
            tags.sort(key=lambda x: x['count'], reverse=True)

            genres = [g.get('name') for g in data.get('genres', [])]

            related_artists = []
            for relation in data.get('relations', []):
                if relation.get('type') == 'member of band':
                    target = relation.get('artist', {})
                    if target:
                        related_artists.append({
                            'id': target.get('id'),
                            'name': target.get('name'),
                            'type': 'member_of'
                        })

            urls = {}
            for relation in data.get('relations', []):
                if relation.get('type') == 'wikidata':
                    url = relation.get('url', {}).get('resource', '')
                    if url:
                        wikidata_id = url.split('/')[-1]
                        urls['wikidata'] = wikidata_id
                elif relation.get('type') == 'wikipedia':
                    urls['wikipedia'] = relation.get('url', {}).get('resource', '')

            area = data.get('area', {})
            country = data.get('country') or area.get('name')

            return {
                'id': data.get('id'),
                'name': data.get('name'),
                'type': data.get('type'),
                'country': country,
                'formed_year': formed_year,
                'disbanded_year': disbanded_year,
                'tags': tags[:10],
                'genres': genres,
                'related_artists': related_artists,
                'wikidata_id': urls.get('wikidata'),
                'wikipedia_url': urls.get('wikipedia'),
                'disambiguation': data.get('disambiguation', '')
            }

        except ExternalDataError:
            return None

    def get_artist_tags(self, mbid: str) -> list[str]:
        """Get tag names for an artist."""
        details = self.get_artist_details(mbid)
        if details and details.get('tags'):
            return [tag['name'] for tag in details['tags']]
        return []


class WikidataClient:
    """Client for Wikidata API (descriptions, influences, etc.)."""

    SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
    API_ENDPOINT = "https://www.wikidata.org/w/api.php"
    USER_AGENT = "NextTrack/1.0 (University Project - Music Recommendation API)"

    def get_entity(self, wikidata_id: str, language: str = 'en') -> Optional[WikidataEntity]:
        """Fetch entity data by Wikidata Q-ID."""
        try:
            response = resilient_get(
                self.API_ENDPOINT,
                params={
                    'action': 'wbgetentities',
                    'ids': wikidata_id,
                    'format': 'json',
                    'languages': language,
                    'props': 'labels|descriptions|claims'
                },
                headers={'User-Agent': self.USER_AGENT},
            )

            response.raise_for_status()
            data = response.json()

            entities = data.get('entities', {})
            if wikidata_id not in entities:
                return None

            entity = entities[wikidata_id]

            labels = entity.get('labels', {})
            descriptions = entity.get('descriptions', {})

            label = labels.get(language, {}).get('value', '')
            description = descriptions.get(language, {}).get('value', '')

            claims = entity.get('claims', {})

            formed_year = None
            if 'P571' in claims:
                try:
                    time_value = claims['P571'][0]['mainsnak']['datavalue']['value']['time']
                    formed_year = int(time_value[1:5])
                except (KeyError, IndexError, ValueError):
                    pass
            if not formed_year and 'P569' in claims:
                try:
                    time_value = claims['P569'][0]['mainsnak']['datavalue']['value']['time']
                    formed_year = int(time_value[1:5])
                except (KeyError, IndexError, ValueError):
                    pass

            genres = []
            if 'P136' in claims:
                for claim in claims['P136']:
                    try:
                        genre_id = claim['mainsnak']['datavalue']['value']['id']
                        genres.append(genre_id)
                    except (KeyError, TypeError):
                        pass

            country = None
            for prop in ['P495', 'P27']:
                if prop in claims:
                    try:
                        country_id = claims[prop][0]['mainsnak']['datavalue']['value']['id']
                        country = country_id
                        break
                    except (KeyError, IndexError, TypeError):
                        pass

            influenced_by_ids = []
            if 'P737' in claims:
                for claim in claims['P737']:
                    try:
                        influence_id = claim['mainsnak']['datavalue']['value']['id']
                        influenced_by_ids.append(influence_id)
                    except (KeyError, TypeError):
                        pass

            return {
                'id': wikidata_id,
                'label': label,
                'description': description,
                'formed_year': formed_year,
                'genre_ids': genres,
                'country_id': country,
                'influenced_by_ids': influenced_by_ids
            }

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Wikidata request failed: {e}")
            return None

    def get_entity_labels(self, entity_ids: list[str], language: str = 'en') -> dict[str, str]:
        """Resolve Wikidata entity IDs to their display names."""
        if not entity_ids:
            return {}

        ids_string = '|'.join(entity_ids[:50])

        try:
            response = resilient_get(
                self.API_ENDPOINT,
                params={
                    'action': 'wbgetentities',
                    'ids': ids_string,
                    'format': 'json',
                    'languages': language,
                    'props': 'labels'
                },
                headers={'User-Agent': self.USER_AGENT},
            )

            response.raise_for_status()
            data = response.json()

            result = {}
            entities = data.get('entities', {})
            for entity_id, entity_data in entities.items():
                labels = entity_data.get('labels', {})
                label = labels.get(language, {}).get('value', '')
                if label:
                    result[entity_id] = label

            return result

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Wikidata label resolution failed: {e}")
            return {}

    def search_artist(self, name: str, limit: int = 5) -> list[dict[str, str]]:
        """Search for entities by name."""
        try:
            response = resilient_get(
                self.API_ENDPOINT,
                params={
                    'action': 'wbsearchentities',
                    'search': name,
                    'type': 'item',
                    'language': 'en',
                    'format': 'json',
                    'limit': limit
                },
                headers={'User-Agent': self.USER_AGENT},
            )

            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get('search', []):
                results.append({
                    'id': item.get('id'),
                    'label': item.get('label'),
                    'description': item.get('description', '')
                })

            return results

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Wikidata search failed: {e}")
            return []

    def get_artist_by_musicbrainz_id(self, mbid: str) -> Optional[WikidataEntity]:
        """Look up a Wikidata entity by MusicBrainz ID."""
        sparql_query = f"""
        SELECT ?item ?itemLabel ?itemDescription WHERE {{
          ?item wdt:P434 "{mbid}".
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}
        LIMIT 1
        """

        try:
            response = resilient_get(
                self.SPARQL_ENDPOINT,
                params={'query': sparql_query, 'format': 'json'},
                headers={'User-Agent': self.USER_AGENT},
            )

            response.raise_for_status()
            data = response.json()

            bindings = data.get('results', {}).get('bindings', [])
            if not bindings:
                return None

            result = bindings[0]
            wikidata_url = result.get('item', {}).get('value', '')
            wikidata_id = wikidata_url.split('/')[-1] if wikidata_url else None

            if wikidata_id:
                return self.get_entity(wikidata_id)

            return None

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Wikidata SPARQL query failed: {e}")
            return None


class LastFmClient:
    """Client for Last.fm API (similar artists, tags, listener stats)."""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        from django.conf import settings
        self._api_key = getattr(settings, 'LASTFM_API_KEY', '')
        self._available = bool(self._api_key)
        if not self._available:
            logger.info("Last.fm API key not configured - Last.fm features disabled")

    @property
    def is_available(self) -> bool:
        """Check if Last.fm API key is set."""
        return self._available

    def _make_request(self, method: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Make a request to Last.fm."""
        if not self._available:
            return None

        if params is None:
            params = {}

        params.update({
            'method': method,
            'api_key': self._api_key,
            'format': 'json'
        })

        try:
            response = resilient_get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

            if 'error' in data:
                logger.warning(f"Last.fm API error: {data.get('message', 'Unknown error')}")
                return None

            return data

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Last.fm request failed: {e}")
            return None

    def get_similar_artists(self, artist_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get similar artists from Last.fm."""
        data = self._make_request('artist.getsimilar', {
            'artist': artist_name,
            'limit': limit
        })

        if not data:
            return []

        similar = data.get('similarartists', {}).get('artist', [])

        if isinstance(similar, dict):
            similar = [similar]

        return [
            {
                'name': a.get('name', ''),
                'match': float(a.get('match', 0)),
                'url': a.get('url', '')
            }
            for a in similar if a.get('name')
        ]

    def get_artist_tags(self, artist_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get community tags for an artist."""
        data = self._make_request('artist.gettoptags', {
            'artist': artist_name
        })

        if not data:
            return []

        tags = data.get('toptags', {}).get('tag', [])

        if isinstance(tags, dict):
            tags = [tags]

        return [
            {
                'name': t.get('name', ''),
                'count': int(t.get('count', 0))
            }
            for t in tags[:limit] if t.get('name')
        ]

    def get_artist_info(self, artist_name: str) -> Optional[LastFmArtistInfo]:
        """Get full artist info including bio, tags, and stats."""
        data = self._make_request('artist.getinfo', {
            'artist': artist_name
        })

        if not data or 'artist' not in data:
            return None

        artist = data['artist']

        bio = artist.get('bio', {})
        summary = bio.get('summary', '')
        if summary:
            import re
            summary = re.sub(r'<[^>]+>', '', summary)
            summary = summary.split('<a href=')[0].strip()

        tags = artist.get('tags', {}).get('tag', [])
        if isinstance(tags, dict):
            tags = [tags]
        tag_names = [t.get('name', '') for t in tags if t.get('name')]

        similar = artist.get('similar', {}).get('artist', [])
        if isinstance(similar, dict):
            similar = [similar]
        similar_names = [a.get('name', '') for a in similar if a.get('name')]

        return {
            'name': artist.get('name', artist_name),
            'listeners': int(artist.get('stats', {}).get('listeners', 0)),
            'playcount': int(artist.get('stats', {}).get('playcount', 0)),
            'bio': summary[:500] if summary else None,
            'tags': tag_names[:10],
            'similar_artists': similar_names[:5],
            'url': artist.get('url', '')
        }


class GeniusClient:
    """Client for Genius API (song/artist info)."""

    BASE_URL = "https://api.genius.com"

    def __init__(self):
        from django.conf import settings
        self._access_token = getattr(settings, 'GENIUS_ACCESS_TOKEN', '')
        self._available = bool(self._access_token)
        if not self._available:
            logger.info("Genius API token not configured - Genius features disabled")

    @property
    def is_available(self) -> bool:
        """Check if Genius API token is set."""
        return self._available

    def _make_request(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Make a request to Genius."""
        if not self._available:
            return None

        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {self._access_token}'
        }

        try:
            response = resilient_get(
                url,
                params=params or {},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            if data.get('meta', {}).get('status') != 200:
                logger.warning(f"Genius API error: {data}")
                return None

            return data.get('response', {})

        except (ExternalDataError, requests.RequestException) as e:
            logger.error(f"Genius request failed: {e}")
            return None

    def search_song(self, query: str, limit: int = 5) -> list[GeniusSongResult]:
        """Search for songs on Genius."""
        data = self._make_request('search', {'q': query})

        if not data:
            return []

        hits = data.get('hits', [])[:limit]

        return [
            {
                'title': hit.get('result', {}).get('title', ''),
                'artist': hit.get('result', {}).get('primary_artist', {}).get('name', ''),
                'url': hit.get('result', {}).get('url', ''),
                'thumbnail': hit.get('result', {}).get('song_art_image_thumbnail_url', ''),
                'genius_id': hit.get('result', {}).get('id')
            }
            for hit in hits if hit.get('result')
        ]

    def get_song_info(self, song_id: int) -> Optional[dict[str, Any]]:
        """Get detailed song info from Genius."""
        data = self._make_request(f'songs/{song_id}')

        if not data or 'song' not in data:
            return None

        song = data['song']

        description = song.get('description', {})
        if isinstance(description, dict):
            desc_text = description.get('plain', '')
        else:
            desc_text = str(description) if description else ''

        return {
            'title': song.get('title', ''),
            'artist': song.get('primary_artist', {}).get('name', ''),
            'album': song.get('album', {}).get('name') if song.get('album') else None,
            'release_date': song.get('release_date_for_display'),
            'description': desc_text[:500] if desc_text else None,
            'url': song.get('url', ''),
            'annotation_count': song.get('annotation_count', 0),
            'pageviews': song.get('stats', {}).get('pageviews', 0)
        }

    def get_artist_info(self, artist_name: str) -> Optional[GeniusArtistInfo]:
        """Get artist info by searching for them on Genius."""
        data = self._make_request('search', {'q': artist_name})

        if not data:
            return None

        hits = data.get('hits', [])
        artist_id = None

        for hit in hits:
            result = hit.get('result', {})
            primary_artist = result.get('primary_artist', {})
            if primary_artist.get('name', '').lower() == artist_name.lower():
                artist_id = primary_artist.get('id')
                break

        if not artist_id and hits:
            artist_id = hits[0].get('result', {}).get('primary_artist', {}).get('id')

        if not artist_id:
            return None

        artist_data = self._make_request(f'artists/{artist_id}')

        if not artist_data or 'artist' not in artist_data:
            return None

        artist = artist_data['artist']

        description = artist.get('description', {})
        if isinstance(description, dict):
            desc_text = description.get('plain', '')
        else:
            desc_text = str(description) if description else ''

        return {
            'name': artist.get('name', ''),
            'url': artist.get('url', ''),
            'image_url': artist.get('image_url', ''),
            'description': desc_text[:500] if desc_text else None,
            'instagram': artist.get('instagram_name'),
            'twitter': artist.get('twitter_name'),
            'followers_count': artist.get('followers_count', 0)
        }


def enrich_artist_from_external_sources(artist_name: str) -> EnrichmentResult:
    """Fetch and merge artist info from MusicBrainz and Wikidata."""
    result = {
        'name': artist_name,
        'musicbrainz': None,
        'wikidata': None,
        'combined': {}
    }

    mb_client = MusicBrainzClient()
    mb_results = mb_client.search_artist(artist_name, limit=3)

    if mb_results:
        best_match = mb_results[0]
        mbid = best_match.get('id')

        if mbid:
            mb_details = mb_client.get_artist_details(mbid)
            if mb_details:
                result['musicbrainz'] = mb_details

                if mb_details.get('wikidata_id'):
                    wd_client = WikidataClient()
                    wd_data = wd_client.get_entity(mb_details['wikidata_id'])
                    if wd_data:
                        result['wikidata'] = wd_data

    combined = {}

    if result['musicbrainz']:
        mb = result['musicbrainz']
        combined['musicbrainz_id'] = mb.get('id')
        combined['name'] = mb.get('name', artist_name)
        combined['type'] = mb.get('type')
        combined['country'] = mb.get('country')
        combined['formed_year'] = mb.get('formed_year')
        combined['disbanded_year'] = mb.get('disbanded_year')
        combined['tags'] = mb.get('tags', [])
        combined['wikidata_id'] = mb.get('wikidata_id')

    if result['wikidata']:
        wd = result['wikidata']
        if not combined.get('wikidata_id'):
            combined['wikidata_id'] = wd.get('id')
        combined['description'] = wd.get('description')
        if not combined.get('formed_year'):
            combined['formed_year'] = wd.get('formed_year')

    result['combined'] = combined
    return result


class LiveExternalDataService:
    """Fetches external artist data with Redis caching."""

    CACHE_TTL = 86400  # 24 hours
    CACHE_VERSION = 2  # Increment to invalidate old cache entries

    def __init__(self):
        self.mb_client = MusicBrainzClient()
        self.wd_client = WikidataClient()
        self.lastfm_client = LastFmClient()
        self.genius_client = GeniusClient()
        self._cache = None

    @property
    def cache(self):
        """Lazy-load the cache backend."""
        if self._cache is None:
            try:
                from django.core.cache import cache
                self._cache = cache
            except Exception as e:
                logger.warning(f"Redis cache unavailable: {e}")
                self._cache = False
        return self._cache

    def _cache_key(self, artist_name: str) -> str:
        """Generate a normalized cache key for an artist."""
        normalized = artist_name.lower().strip().replace(' ', '_')
        return f"ext_artist:v{self.CACHE_VERSION}:{normalized}"

    def get_artist_info_live(self, artist_name: str) -> ArtistExternalData:
        """Fetch artist info from all sources, with caching."""
        cache_key = self._cache_key(artist_name)
        if self.cache:
            try:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    logger.debug(f"Cache hit for artist: {artist_name}")
                    return cached
            except Exception as e:
                logger.warning(f"Cache read error: {e}")

        logger.info(f"Live fetch for artist: {artist_name}")
        result = {
            'name': artist_name,
            'country': None,
            'type': None,
            'formed_year': None,
            'tags': [],
            'genres': [],
            'description': None,
            'similar_artists': [],
            'influenced_by': [],
            'lastfm_tags': [],
            'listeners': 0,
            'genius_url': None,
            'data_sources': [],
            'source': 'external'
        }

        try:
            mb_results = self.mb_client.search_artist(artist_name, limit=3)
            if mb_results:
                best_match = mb_results[0]
                mbid = best_match.get('id')

                if mbid:
                    details = self.mb_client.get_artist_details(mbid)
                    if details:
                        result['country'] = details.get('country')
                        result['type'] = details.get('type')
                        result['formed_year'] = details.get('formed_year')
                        result['tags'] = [t['name'] for t in details.get('tags', [])[:10]]
                        result['genres'] = details.get('genres', [])
                        result['data_sources'].append('musicbrainz')

                        wikidata_id = details.get('wikidata_id')
                        if wikidata_id:
                            wd_data = self.wd_client.get_entity(wikidata_id)
                            if wd_data:
                                result['description'] = wd_data.get('description')
                                result['data_sources'].append('wikidata')

                                if not result['formed_year']:
                                    result['formed_year'] = wd_data.get('formed_year')

                                influenced_by_ids = wd_data.get('influenced_by_ids', [])
                                if influenced_by_ids:
                                    labels = self.wd_client.get_entity_labels(influenced_by_ids[:5])
                                    result['influenced_by'] = [
                                        labels.get(qid, qid)
                                        for qid in influenced_by_ids[:5]
                                        if labels.get(qid)
                                    ]

            if self.lastfm_client.is_available:
                try:
                    similar = self.lastfm_client.get_similar_artists(artist_name, limit=5)
                    if similar:
                        result['similar_artists'] = [a['name'] for a in similar]
                        if 'lastfm' not in result['data_sources']:
                            result['data_sources'].append('lastfm')

                    tags = self.lastfm_client.get_artist_tags(artist_name, limit=10)
                    if tags:
                        result['lastfm_tags'] = [t['name'] for t in tags]
                        if 'lastfm' not in result['data_sources']:
                            result['data_sources'].append('lastfm')

                    info = self.lastfm_client.get_artist_info(artist_name)
                    if info:
                        result['listeners'] = info.get('listeners', 0)
                        if not result['description'] and info.get('bio'):
                            result['description'] = info['bio']

                except Exception as e:
                    logger.warning(f"Last.fm fetch failed for {artist_name}: {e}")

            if self.genius_client.is_available:
                try:
                    genius_info = self.genius_client.get_artist_info(artist_name)
                    if genius_info:
                        result['genius_url'] = genius_info.get('url')
                        result['data_sources'].append('genius')

                        if not result['description'] and genius_info.get('description'):
                            result['description'] = genius_info['description']

                except Exception as e:
                    logger.warning(f"Genius fetch failed for {artist_name}: {e}")

        except (ExternalDataError, RateLimitError) as e:
            logger.warning(f"External API error for {artist_name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching {artist_name}: {e}")

        if self.cache:
            try:
                self.cache.set(cache_key, result, timeout=self.CACHE_TTL)
            except Exception as e:
                logger.warning(f"Cache write error: {e}")

        return result

    def get_artist_tags(self, artist_name: str) -> list[str]:
        """Get just the tags for an artist."""
        info = self.get_artist_info_live(artist_name)
        return info.get('tags', [])

    def get_track_lyrics_themes(self, track_title, artist_name):
        """Fetch lyric themes from Genius API (stub for future enrichment)."""
        if not getattr(django_settings, 'GENIUS_ACCESS_TOKEN', ''):
            return None
        # Future: Call Genius API to fetch lyrics, extract themes
        return {'status': 'not_implemented', 'track': track_title, 'artist': artist_name}

    def batch_get_artist_info(self, artist_names: list[str], max_live_fetches: int = 5) -> dict[str, ArtistExternalData]:
        """Batch fetch artist info, limiting live API calls to avoid rate limits."""
        results = {}
        live_fetches = 0

        for name in artist_names:
            cache_key = self._cache_key(name)

            if self.cache:
                try:
                    cached = self.cache.get(cache_key)
                    if cached is not None:
                        results[name] = cached
                        continue
                except Exception:
                    pass

            if live_fetches < max_live_fetches:
                results[name] = self.get_artist_info_live(name)
                live_fetches += 1
            else:
                results[name] = {
                    'name': name,
                    'source': 'rate_limited'
                }

        return results


_live_service = None


def get_live_external_service() -> LiveExternalDataService:
    """Get or create the singleton LiveExternalDataService."""
    global _live_service
    if _live_service is None:
        _live_service = LiveExternalDataService()
    return _live_service
