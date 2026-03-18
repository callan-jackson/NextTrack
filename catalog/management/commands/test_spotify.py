"""
Spotify API Diagnostic Management Command.

Provides comprehensive testing of Spotify Web API integration, validating:
1. Environment configuration (credentials)
2. Authentication flow (OAuth Client Credentials)
3. Search endpoint functionality
4. Audio Features endpoint access

API Deprecation Notice:
As of November 2024, Spotify deprecated the Audio Features endpoint for
applications created after this date. New applications receive HTTP 403
Forbidden when requesting audio features. This command detects this
restriction and provides appropriate guidance.

Usage Examples:
    # Run full diagnostic
    python manage.py test_spotify

    # Test with custom search query
    python manage.py test_spotify --search "Bohemian Rhapsody"

    # Test audio features for specific track
    python manage.py test_spotify --track-id 4u7EnebtmKWzUH433cf5Qv
"""

from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    """
    Diagnostic command for Spotify API integration validation.

    Executes sequential tests to identify configuration issues,
    authentication failures, and API access restrictions.
    """
    help = 'Test Spotify API connection and diagnose issues'

    def add_arguments(self, parser):
        """
        Define optional command-line arguments.

        Arguments allow customization of test parameters for
        targeted debugging scenarios.
        """
        parser.add_argument(
            '--search',
            type=str,
            help='Search query to test (e.g., "Bohemian Rhapsody")'
        )
        parser.add_argument(
            '--track-id',
            type=str,
            help='Spotify track ID to fetch audio features for'
        )

    def handle(self, *args, **options):
        """
        Execute diagnostic test sequence.

        Tests are ordered to identify failures at the earliest point,
        providing clear diagnostic information for troubleshooting.
        """
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('SPOTIFY API DIAGNOSTIC'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        # =====================================================================
        # STEP 1: Verify credential configuration
        # =====================================================================
        self.stdout.write(self.style.MIGRATE_HEADING('Step 1: Checking Credentials'))

        client_id = getattr(settings, 'SPOTIFY_CLIENT_ID', '')
        client_secret = getattr(settings, 'SPOTIFY_CLIENT_SECRET', '')

        # Display masked credentials for verification
        if client_id:
            masked_id = client_id[:8] + '...' if len(client_id) > 8 else client_id
            self.stdout.write(self.style.SUCCESS(f'  SPOTIFY_CLIENT_ID: {masked_id} (SET)'))
        else:
            self.stdout.write(self.style.ERROR('  SPOTIFY_CLIENT_ID: NOT SET'))

        if client_secret:
            masked_secret = client_secret[:4] + '****' if len(client_secret) > 4 else '****'
            self.stdout.write(self.style.SUCCESS(f'  SPOTIFY_CLIENT_SECRET: {masked_secret} (SET)'))
        else:
            self.stdout.write(self.style.ERROR('  SPOTIFY_CLIENT_SECRET: NOT SET'))

        # Terminate if credentials are missing
        if not client_id or not client_secret:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('CREDENTIALS MISSING!'))
            self.stdout.write('')
            self.stdout.write('Configuration Steps:')
            self.stdout.write('1. Navigate to https://developer.spotify.com/dashboard')
            self.stdout.write('2. Create a new application')
            self.stdout.write('3. Copy Client ID and Client Secret')
            self.stdout.write('4. Add to .env file:')
            self.stdout.write('   SPOTIFY_CLIENT_ID=your_client_id')
            self.stdout.write('   SPOTIFY_CLIENT_SECRET=your_client_secret')
            self.stdout.write('5. Restart the web container:')
            self.stdout.write('   docker-compose restart web')
            return

        self.stdout.write('')

        # =====================================================================
        # STEP 2: Test OAuth authentication
        # =====================================================================
        self.stdout.write(self.style.MIGRATE_HEADING('Step 2: Testing Authentication'))

        try:
            from catalog.spotify_client import SpotifyClient, SpotifyAuthError

            client = SpotifyClient()

            if not client.is_configured:
                self.stdout.write(self.style.ERROR('  Client reports not configured'))
                return

            # Attempt token acquisition
            token = client._get_token()
            if token:
                masked_token = token[:20] + '...' if len(token) > 20 else token
                self.stdout.write(self.style.SUCCESS(f'  Token obtained: {masked_token}'))
            else:
                self.stdout.write(self.style.ERROR('  Failed to obtain token'))
                return

        except SpotifyAuthError as e:
            self.stdout.write(self.style.ERROR(f'  Authentication failed: {e}'))
            self.stdout.write('')
            self.stdout.write('Possible causes:')
            self.stdout.write('- Invalid Client ID or Secret')
            self.stdout.write('- Spotify Developer account issues')
            self.stdout.write('- Network connectivity problems')
            return

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Unexpected error: {e}'))
            return

        self.stdout.write('')

        # =====================================================================
        # STEP 3: Test search endpoint
        # =====================================================================
        search_query = options.get('search') or 'Bohemian Rhapsody'
        self.stdout.write(self.style.MIGRATE_HEADING(f'Step 3: Testing Search ("{search_query}")'))

        try:
            results = client.search_tracks(search_query, limit=5)

            if results:
                self.stdout.write(self.style.SUCCESS(f'  Found {len(results)} tracks:'))
                for i, track in enumerate(results[:5], 1):
                    name = track.get('name', 'Unknown')
                    artists = ', '.join(a.get('name', '') for a in track.get('artists', []))
                    track_id = track.get('id', 'N/A')
                    self.stdout.write(f'    {i}. {name} - {artists}')
                    self.stdout.write(f'       ID: {track_id}')
            else:
                self.stdout.write(self.style.WARNING('  No results found'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Search failed: {e}'))

        self.stdout.write('')

        # =====================================================================
        # STEP 4: Test audio features endpoint (restricted for new apps)
        # =====================================================================
        track_id = options.get('track_id')
        if not track_id and results:
            track_id = results[0].get('id')

        audio_features_available = False
        if track_id:
            self.stdout.write(self.style.MIGRATE_HEADING(f'Step 4: Testing Audio Features ({track_id})'))

            try:
                features = client.get_audio_features(track_id)

                if features:
                    audio_features_available = True
                    self.stdout.write(self.style.SUCCESS('  Audio features retrieved:'))
                    self.stdout.write(f'    Valence:      {features.get("valence", "N/A")}')
                    self.stdout.write(f'    Energy:       {features.get("energy", "N/A")}')
                    self.stdout.write(f'    Danceability: {features.get("danceability", "N/A")}')
                    self.stdout.write(f'    Acousticness: {features.get("acousticness", "N/A")}')
                    self.stdout.write(f'    Tempo:        {features.get("tempo", "N/A")} BPM')
                else:
                    self.stdout.write(self.style.WARNING('  Audio features NOT available (403 Forbidden)'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Audio features failed: {e}'))

        self.stdout.write('')

        # =====================================================================
        # DIAGNOSTIC SUMMARY
        # =====================================================================
        self.stdout.write(self.style.HTTP_INFO('=' * 60))

        if audio_features_available:
            self.stdout.write(self.style.SUCCESS('FULL INTEGRATION - All features available!'))
            self.stdout.write(self.style.HTTP_INFO('=' * 60))
            self.stdout.write('')
            self.stdout.write('Hybrid search capabilities:')
            self.stdout.write('- Primary: Local database query')
            self.stdout.write('- Fallback: Spotify API when local results < 5')
            self.stdout.write('- Auto-ingestion: New tracks with audio features')
        else:
            self.stdout.write(self.style.WARNING('PARTIAL INTEGRATION - Audio Features restricted'))
            self.stdout.write(self.style.HTTP_INFO('=' * 60))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('IMPORTANT: Spotify API Restriction Detected'))
            self.stdout.write('')
            self.stdout.write('As of November 2024, Spotify deprecated the Audio Features')
            self.stdout.write('endpoint for new applications. Your app returns 403 Forbidden.')
            self.stdout.write('')
            self.stdout.write('Impact Assessment:')
            self.stdout.write('- Spotify SEARCH: Operational')
            self.stdout.write('- Audio Features: BLOCKED (403 Forbidden)')
            self.stdout.write('- New track ingestion: NOT POSSIBLE (features required)')
            self.stdout.write('')
            self.stdout.write('Remediation Options:')
            self.stdout.write('1. Request Extended Quota at developer.spotify.com/dashboard')
            self.stdout.write('   (Select app > Request Extended Quota Mode)')
            self.stdout.write('2. Operate with local database only (~114k tracks from CSV)')
            self.stdout.write('')
            self.stdout.write('The recommendation system remains functional with existing data.')
