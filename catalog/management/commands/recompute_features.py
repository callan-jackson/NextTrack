"""Management command to recompute audio features for unanalyzed tracks."""

import logging
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Attempt to fetch real audio features for tracks with default values'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Recompute all tracks, not just unanalyzed')
        parser.add_argument('--batch-size', type=int, default=100, help='Batch size for Spotify API calls')

    def handle(self, *args, **options):
        from catalog.models import Track

        if options['all']:
            tracks = Track.objects.all()
        else:
            tracks = Track.objects.filter(is_audio_analyzed=False)

        total = tracks.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No tracks need recomputation.'))
            return

        self.stdout.write(f'Found {total} tracks to process.')

        try:
            from catalog.spotify_client import SpotifyClient
            client = SpotifyClient()
            if not client.is_configured:
                self.stdout.write(self.style.ERROR('Spotify client not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Could not initialize Spotify client: {e}'))
            return

        batch_size = options['batch_size']
        track_ids = list(tracks.values_list('id', flat=True))
        updated = 0
        attempted = 0

        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i:i + batch_size]
            attempted += len(batch)

            try:
                features_map = client.get_audio_features_batch(batch)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Batch {i // batch_size + 1} failed: {e}'))
                continue

            for track_id in batch:
                features = features_map.get(track_id)
                if not features:
                    continue

                try:
                    track = Track.objects.get(id=track_id)
                    track.energy = features.get('energy', track.energy)
                    track.valence = features.get('valence', track.valence)
                    track.danceability = features.get('danceability', track.danceability)
                    track.acousticness = features.get('acousticness', track.acousticness)
                    track.tempo = features.get('tempo', track.tempo)
                    track.loudness = features.get('loudness', track.loudness)
                    track.is_audio_analyzed = True
                    track.save(update_fields=[
                        'energy', 'valence', 'danceability', 'acousticness',
                        'tempo', 'loudness', 'is_audio_analyzed',
                    ])
                    updated += 1
                except Exception as e:
                    logger.error(f'Failed to update track {track_id}: {e}')

            self.stdout.write(f'  Processed batch {i // batch_size + 1}: {updated} updated so far')

        self.stdout.write(self.style.SUCCESS(
            f'Done. Attempted: {attempted}, Updated: {updated}, Still missing: {attempted - updated}'
        ))
