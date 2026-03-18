"""
High-performance data ingestion command using transactional bulk loading.

Performance Techniques:
1. pandas for fast CSV reading
2. bulk_create with ignore_conflicts for single SQL queries
3. Batch processing (5000 records) for memory management
4. transaction.atomic() for M2M relationship handling

ID Generation Strategy:
- Uses Django's slugify() for deterministic, human-readable IDs
- Avoids Python's hash() which is non-deterministic across restarts
- Ensures consistent Foreign Key relationships across environments
"""

import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
import pandas as pd

from catalog.models import Genre, Artist, Track

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ingest Spotify tracks dataset into database using bulk operations'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_path',
            type=str,
            help='Path to the dataset.csv file'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Batch size for bulk operations (default: 5000)'
        )
        parser.add_argument(
            '--fresh',
            action='store_true',
            help='Wipe existing data before ingestion (ensures clean slate)'
        )
        parser.add_argument(
            '--warm-cache',
            action='store_true',
            help='Trigger cache warming for popular tracks after ingestion'
        )
        parser.add_argument(
            '--popularity-threshold',
            type=int,
            default=70,
            help='Minimum popularity score for cache warming (default: 70)'
        )

    def handle(self, *args, **options):
        csv_path = options['csv_path']
        batch_size = options['batch_size']

        # CRITICAL: Wipe database if --fresh flag is set
        # This ensures no duplicate data from previous ingestions
        if options.get('fresh'):
            self.stdout.write(self.style.WARNING('Wiping existing data for fresh ingestion...'))
            # Delete in correct order to respect foreign key constraints
            Track.genres.through.objects.all().delete()
            Track.objects.all().delete()
            Artist.objects.all().delete()
            Genre.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Database wiped successfully.'))

        self.stdout.write(f'Reading CSV from {csv_path}...')

        # Step 1: Read CSV with pandas (faster than Python csv module)
        df = pd.read_csv(csv_path)
        initial_rows = len(df)
        self.stdout.write(f'Loaded {initial_rows} rows from CSV')

        # CRUCIAL FIX: Deduplicate by track_id
        # The CSV contains duplicate track_id entries (same song in different playlists)
        # This prevents database constraint errors and data clutter
        df.drop_duplicates(subset=['track_id'], keep='first', inplace=True)
        total_rows = len(df)
        duplicates_removed = initial_rows - total_rows
        self.stdout.write(self.style.WARNING(
            f'Removed {duplicates_removed} duplicate tracks. {total_rows} unique tracks remaining.'
        ))

        # Clean data - handle NaN values
        df = df.fillna({
            'track_id': '',
            'artists': 'Unknown',
            'track_name': 'Unknown',
            'track_genre': 'unknown',
            'popularity': 0,
            'danceability': 0.0,
            'energy': 0.0,
            'loudness': 0.0,
            'valence': 0.0,
            'tempo': 0.0,
            'acousticness': 0.0,
        })

        # Step 2: Extract and bulk create unique genres
        self.stdout.write('Processing genres...')
        unique_genres = df['track_genre'].unique()
        genre_objects = [Genre(name=g) for g in unique_genres if g]
        Genre.objects.bulk_create(genre_objects, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f'Created {len(unique_genres)} genres'))

        # Build genre lookup dict
        genre_lookup = {g.name: g for g in Genre.objects.all()}

        # Step 3: Extract and bulk create unique artists
        # Uses slugify() for deterministic, human-readable, SEO-friendly IDs
        # This ensures consistent IDs across server restarts and environments
        self.stdout.write('Processing artists...')
        artist_data = df[['artists']].drop_duplicates()

        artist_objects = []
        artist_id_map = {}
        artist_slug_counter = {}  # Handle duplicate slugs

        for idx, row in artist_data.iterrows():
            artist_name = str(row['artists'])[:255]

            # Generate deterministic slug-based ID
            # slugify("The Beatles") -> "the-beatles"
            # slugify("Daft Punk") -> "daft-punk"
            artist_slug = slugify(artist_name)

            # Fallback for empty slugs (e.g., artist name was "???" or non-ASCII only)
            if not artist_slug:
                artist_slug = f"artist-{idx}"

            # Handle duplicate slugs (rare but possible with special characters)
            # e.g., "Björk" and "Bjork" might both slugify to "bjork"
            if artist_slug in artist_slug_counter:
                artist_slug_counter[artist_slug] += 1
                artist_id = f"{artist_slug}-{artist_slug_counter[artist_slug]}"
            else:
                artist_slug_counter[artist_slug] = 0
                artist_id = artist_slug

            # Truncate to fit CharField(max_length=100)
            artist_id = artist_id[:100]

            if artist_name not in artist_id_map:
                artist_id_map[artist_name] = artist_id
                artist_objects.append(Artist(
                    id=artist_id,
                    name=artist_name,
                    popularity=0  # Will update later with average
                ))

        Artist.objects.bulk_create(artist_objects, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f'Created {len(artist_objects)} artists'))

        # Rebuild artist lookup
        artist_lookup = {a.name: a for a in Artist.objects.all()}

        # Step 4: Bulk create tracks in batches
        self.stdout.write('Processing tracks in batches...')
        tracks_created = 0
        track_genre_relations = []  # Store (track_id, genre_name) tuples

        for batch_start in range(0, total_rows, batch_size):
            batch_end = min(batch_start + batch_size, total_rows)
            batch_df = df.iloc[batch_start:batch_end]

            track_objects = []
            batch_relations = []

            for idx, row in batch_df.iterrows():
                track_id = str(row['track_id'])
                if not track_id:
                    continue

                artist_name = str(row['artists'])[:255]
                artist = artist_lookup.get(artist_name)
                if not artist:
                    continue

                track = Track(
                    id=track_id,
                    title=str(row['track_name'])[:500],
                    artist=artist,
                    danceability=float(row['danceability']),
                    energy=float(row['energy']),
                    loudness=float(row['loudness']),
                    valence=float(row['valence']),
                    tempo=float(row['tempo']),
                    acousticness=float(row['acousticness']),
                    popularity=int(row['popularity']),
                )
                track_objects.append(track)

                # Store genre relation for later
                genre_name = str(row['track_genre'])
                if genre_name in genre_lookup:
                    batch_relations.append((track_id, genre_name))

            # Bulk create tracks
            Track.objects.bulk_create(track_objects, ignore_conflicts=True)
            track_genre_relations.extend(batch_relations)
            tracks_created += len(track_objects)

            self.stdout.write(f'Processed {batch_end}/{total_rows} tracks...')

        self.stdout.write(self.style.SUCCESS(f'Created {tracks_created} tracks'))

        # Step 5: Handle ManyToMany relationships
        self.stdout.write('Setting up genre relationships...')
        self._bulk_add_genres(track_genre_relations, genre_lookup)

        self.stdout.write(self.style.SUCCESS(
            f'Data ingestion complete! '
            f'Genres: {Genre.objects.count()}, '
            f'Artists: {Artist.objects.count()}, '
            f'Tracks: {Track.objects.count()}'
        ))

        # Step 6: Trigger cache warming for popular tracks (optional)
        if options.get('warm_cache'):
            self.stdout.write('Triggering cache warming for popular tracks...')
            from catalog.tasks import warm_cache_for_popular_tracks
            result = warm_cache_for_popular_tracks.delay(
                popularity_threshold=options['popularity_threshold']
            )
            self.stdout.write(self.style.SUCCESS(
                f'Cache warming task queued: {result.id}'
            ))

    @transaction.atomic
    def _bulk_add_genres(self, relations, genre_lookup):
        """
        Add ManyToMany genre relationships using transaction.atomic().
        Django doesn't support bulk_create for M2M, but wrapping in
        atomic() significantly improves performance.
        """
        # Get the through model
        ThroughModel = Track.genres.through

        # Build through model instances
        through_instances = []
        seen = set()

        for track_id, genre_name in relations:
            genre = genre_lookup.get(genre_name)
            if genre and (track_id, genre.id) not in seen:
                through_instances.append(
                    ThroughModel(track_id=track_id, genre_id=genre.id)
                )
                seen.add((track_id, genre.id))

        # Bulk create through model entries
        ThroughModel.objects.bulk_create(through_instances, ignore_conflicts=True)
        self.stdout.write(f'Created {len(through_instances)} track-genre relationships')
