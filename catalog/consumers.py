"""WebSocket consumer for real-time search."""

import json
import logging
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


class SearchConsumer(AsyncWebsocketConsumer):
    """Handles WebSocket connections for search."""

    async def connect(self):
        """Accept connection and send ready message."""
        await self.accept()
        logger.info(f"WebSocket connected: {self.channel_name}")

        await self.send(text_data=json.dumps({
            'type': 'ready',
            'message': 'Ready to search.'
        }))

    async def disconnect(self, close_code):
        """Handle disconnection."""
        logger.info(f"WebSocket disconnected: {self.channel_name}, code: {close_code}")

    async def receive(self, text_data):
        """Handle incoming messages and route to search handler."""
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'search')

            if message_type == 'search':
                query = data.get('query', '').strip()
                await self.handle_search(query)
            else:
                await self.send_error(f"Unknown message type: {message_type}")

        except json.JSONDecodeError:
            await self.send_error("Invalid JSON message")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            await self.send_error(f"Search failed: {str(e)}")

    async def handle_search(self, query):
        """Run search and stream progress updates to the client."""
        if not query or len(query) < 2:
            await self.send_error("Search query must be at least 2 characters")
            return

        if len(query) > 200:
            await self.send_error("Search query must be 200 characters or fewer")
            return

        # Strip HTML tags for defense-in-depth
        import re
        query = re.sub(r'<[^>]+>', '', query).strip()
        if not query:
            await self.send_error("Search query is empty after sanitization")
            return

        await self.send_status("Initializing search...", phase=1, total_phases=4)

        # Local DB search
        await self.send_status(f"Searching local database for \"{query}\"...", phase=2, total_phases=4)
        local_results, seen_keys = await self.search_local_database(query)
        local_count = len(local_results)

        await self.send_status(
            f"Found {local_count} matches in local database",
            phase=2,
            total_phases=4,
            detail=f"{local_count} local tracks"
        )

        # Live provider search
        provider = await self.get_provider_name()
        await self.send_status(
            f"Querying {provider.title()} for additional results...",
            phase=3,
            total_phases=4
        )
        provider_results = await self.search_provider(query, seen_keys)
        provider_count = len(provider_results)

        if provider_count > 0:
            await self.send_status(
                f"Ingested {provider_count} new tracks from {provider.title()}",
                phase=3,
                total_phases=4,
                detail=f"+{provider_count} from {provider.title()}"
            )
        else:
            await self.send_status(
                f"No additional tracks from {provider.title()} (all already in database)",
                phase=3,
                total_phases=4
            )

        # Merge and rank
        await self.send_status("Ranking results by relevance...", phase=4, total_phases=4)
        combined_results = await self.merge_and_rank(local_results, provider_results, query)
        total_count = len(combined_results)

        tracks_data = await self.serialize_tracks(combined_results[:20])

        await self.send(text_data=json.dumps({
            'type': 'results',
            'tracks': tracks_data,
            'count': min(total_count, 20),
            'total_found': total_count,
            'local_count': local_count,
            # Kept under the original key so the existing frontend keeps working.
            'spotify_count': provider_count,
            'provider': provider,
            'provider_count': provider_count,
            'query': query
        }))

        logger.info(f"WebSocket search complete: '{query}' -> {total_count} results")

    async def send_status(self, message, phase=1, total_phases=4, detail=None):
        """Send a progress update to the client."""
        await self.send(text_data=json.dumps({
            'type': 'status',
            'message': message,
            'phase': phase,
            'total_phases': total_phases,
            'progress': int((phase / total_phases) * 100),
            'detail': detail
        }))

    async def send_error(self, message):
        """Send an error message to the client."""
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': message
        }))

    @database_sync_to_async
    def search_local_database(self, query, limit=20):
        """Search local DB for tracks matching the query."""
        from catalog.models import Track

        raw_results = Track.objects.filter(
            Q(title__icontains=query) |
            Q(artist__name__icontains=query) |
            Q(genres__name__icontains=query)
        ).select_related('artist').distinct()[:limit]

        local_results = list(raw_results)

        seen_keys = set()
        for track in local_results:
            key = (track.title.lower().strip(), track.artist.name.lower().strip())
            seen_keys.add(key)

        return local_results, seen_keys

    @database_sync_to_async
    def get_provider_name(self):
        """Name of the configured live-search provider, for status messages."""
        from catalog.services import get_search_provider_name

        return get_search_provider_name()

    @database_sync_to_async
    def search_provider(self, query, existing_keys, limit=20):
        """Search the configured provider and ingest any new tracks found."""
        from catalog.services import _fetch_and_ingest_from_provider

        try:
            return _fetch_and_ingest_from_provider(
                query,
                limit=limit,
                existing_keys=existing_keys
            )
        except Exception as e:
            logger.error(f"Provider search failed: {e}")
            return []

    @database_sync_to_async
    def merge_and_rank(self, local_results, provider_results, query):
        """Combine local + provider results and sort by relevance."""
        combined = local_results + provider_results
        query_lower = query.lower().strip()

        def smart_sort_key(track):
            artist_name = track.artist.name.lower()
            title_name = track.title.lower()

            is_exact_artist = artist_name == query_lower
            is_startswith_artist = artist_name.startswith(query_lower)
            is_exact_title = title_name == query_lower
            is_partial_artist = query_lower in artist_name
            is_partial_title = query_lower in title_name

            return (
                not is_exact_artist,
                not is_startswith_artist,
                not is_exact_title,
                not is_partial_artist,
                not is_partial_title,
                -track.popularity
            )

        combined.sort(key=smart_sort_key)

        # Deduplicate by title + artist (same song can have multiple Spotify IDs)
        seen = set()
        unique = []
        for track in combined:
            key = (track.title.lower().strip(), track.artist.name.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(track)
        return unique

    @database_sync_to_async
    def serialize_tracks(self, tracks):
        """Convert tracks to JSON with source badges (live/catalog/limited)."""
        now = timezone.now()
        one_hour_ago = now - timedelta(hours=1)

        result = []
        for track in tracks:
            if track.is_audio_analyzed:
                # Recently discovered and already analysed.
                if track.created_at and track.created_at > one_hour_ago:
                    source_badge = 'live'
                else:
                    source_badge = 'catalog'
            elif track.preview_url:
                # Queued for audio analysis; features are placeholders for now.
                source_badge = 'pending'
            else:
                source_badge = 'limited'

            genres = list(track.genres.values_list('name', flat=True)[:3])

            result.append({
                'id': track.id,
                'title': track.title,
                'artist_name': track.artist.name,
                'artist_id': track.artist.id,
                'popularity': track.popularity,
                'energy': float(track.energy) if track.energy else 0.5,
                'valence': float(track.valence) if track.valence else 0.5,
                'danceability': float(track.danceability) if track.danceability else 0.5,
                'acousticness': float(track.acousticness) if track.acousticness else 0.5,
                'tempo': float(track.tempo) if track.tempo else 120.0,
                'loudness': float(track.loudness) if track.loudness else -10.0,
                'genres': genres,
                'is_audio_analyzed': track.is_audio_analyzed,
                'source_badge': source_badge,
            })

        return result
