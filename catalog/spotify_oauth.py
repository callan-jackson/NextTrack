"""Spotify OAuth for letting users export playlists to their accounts."""

import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class SpotifyOAuthError(Exception):
    """Raised when OAuth fails."""
    pass


class SpotifyUserClient:
    """Client for user-authenticated Spotify operations."""

    AUTH_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE_URL = "https://api.spotify.com/v1"

    SCOPES = ['playlist-modify-public', 'playlist-modify-private']

    def __init__(self, access_token=None):
        self._client_id = getattr(settings, 'SPOTIFY_CLIENT_ID', None)
        self._client_secret = getattr(settings, 'SPOTIFY_CLIENT_SECRET', None)
        self._access_token = access_token
        self._user_id = None

    @property
    def is_configured(self):
        """Check if Spotify credentials are set in settings."""
        return bool(self._client_id and self._client_secret)

    @property
    def is_authenticated(self):
        """Check if we have a user access token."""
        return bool(self._access_token)

    def get_authorization_url(self, redirect_uri, state=None):
        """Build the Spotify auth URL, returns (url, state)."""
        if not self.is_configured:
            raise SpotifyOAuthError("Spotify credentials not configured")

        if not state:
            state = secrets.token_urlsafe(32)

        params = {
            'client_id': self._client_id,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'scope': ' '.join(self.SCOPES),
            'state': state,
            'show_dialog': 'false',
        }

        url = f"{self.AUTH_URL}?{urlencode(params)}"
        return url, state

    def exchange_code_for_token(self, code, redirect_uri):
        """Exchange auth code for access token after user authorizes."""
        if not self.is_configured:
            raise SpotifyOAuthError("Spotify credentials not configured")

        try:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': redirect_uri,
                },
                auth=(self._client_id, self._client_secret),
                timeout=10
            )

            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.status_code}")
                raise SpotifyOAuthError(f"Token exchange failed: {response.status_code}")

            data = response.json()
            self._access_token = data['access_token']
            return data

        except requests.RequestException as e:
            raise SpotifyOAuthError(f"Network error: {e}")

    def _make_request(self, method, endpoint, json_data=None, params=None):
        """Make authenticated API request."""
        if not self._access_token:
            raise SpotifyOAuthError("No access token")

        url = f"{self.API_BASE_URL}{endpoint}"
        headers = {
            'Authorization': f'Bearer {self._access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.request(
                method, url, headers=headers, json=json_data, params=params, timeout=15
            )

            if response.status_code in (200, 201):
                return response.json() if response.text else {}
            if response.status_code == 204:
                return {}
            if response.status_code == 401:
                raise SpotifyOAuthError("Token expired or invalid")

            raise SpotifyOAuthError(f"API error: {response.status_code}")

        except requests.RequestException as e:
            raise SpotifyOAuthError(f"Network error: {e}")

    def get_current_user(self):
        """Get current user's Spotify profile."""
        data = self._make_request('GET', '/me')
        self._user_id = data.get('id')
        return data

    def create_playlist(self, name, description="", public=True):
        """Create a new playlist in the user's account."""
        if not self._user_id:
            self.get_current_user()

        data = self._make_request(
            'POST',
            f'/users/{self._user_id}/playlists',
            json_data={'name': name, 'description': description, 'public': public}
        )
        logger.info(f"Created playlist: {name}")
        return data

    def add_tracks_to_playlist(self, playlist_id, track_uris):
        """Add tracks to a playlist (max 100 per call)."""
        all_uris = track_uris[:100]
        data = self._make_request(
            'POST',
            f'/playlists/{playlist_id}/tracks',
            json_data={'uris': all_uris}
        )
        logger.info(f"Added {len(all_uris)} tracks to playlist")
        return data

    def export_recommendations(self, track_ids, playlist_name=None, description=None):
        """Create a playlist and add the given tracks to it."""
        if not track_ids:
            return {'success': False, 'error': 'No tracks provided'}

        if not playlist_name:
            from datetime import datetime
            date_str = datetime.now().strftime("%b %d, %Y")
            playlist_name = f"NextTrack Recommendations - {date_str}"

        if not description:
            description = "Personalized recommendations from NextTrack"

        try:
            playlist = self.create_playlist(playlist_name, description, public=False)
            playlist_id = playlist['id']
            playlist_url = playlist['external_urls']['spotify']

            track_uris = [f"spotify:track:{tid}" for tid in track_ids if tid]

            if track_uris:
                self.add_tracks_to_playlist(playlist_id, track_uris)

            return {
                'success': True,
                'playlist_url': playlist_url,
                'playlist_id': playlist_id,
                'tracks_added': len(track_uris),
                'playlist_name': playlist_name
            }

        except SpotifyOAuthError as e:
            logger.error(f"Export failed: {e}")
            return {'success': False, 'error': str(e)}


def get_spotify_user_client(access_token=None):
    """Factory function."""
    return SpotifyUserClient(access_token)
