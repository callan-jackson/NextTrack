"""Management command to seed the database with deterministic test data.

Usage:
    python manage.py seed_test_data
    python manage.py seed_test_data --flush   # wipe existing data first
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Genre, Artist, Track


# Deterministic seed data -- every run produces identical records.
GENRES = [
    "rock", "pop", "electronic", "hip-hop", "jazz",
    "classical", "r-and-b", "country", "metal", "folk",
]

ARTISTS = [
    # (id, name, popularity, country, type, formed_year)
    ("seed-the-echoes", "The Echoes", 82, "US", "Group", 1995),
    ("seed-luna-wave", "Luna Wave", 74, "GB", "Person", 2003),
    ("seed-neon-pulse", "Neon Pulse", 68, "DE", "Group", 2010),
    ("seed-iron-coast", "Iron Coast", 71, "AU", "Group", 1988),
    ("seed-velvet-haze", "Velvet Haze", 65, "JP", "Person", 2015),
    ("seed-midnight-sun", "Midnight Sun", 78, "SE", "Group", 1992),
    ("seed-silver-tide", "Silver Tide", 60, "CA", "Group", 2000),
    ("seed-amber-glow", "Amber Glow", 55, "BR", "Person", 2008),
    ("seed-crystal-rain", "Crystal Rain", 72, "KR", "Group", 2012),
    ("seed-ghost-signal", "Ghost Signal", 63, "FR", "Person", 1999),
]

# Each tuple: (id, title, artist_index, genre_indices, valence, energy,
#               danceability, acousticness, tempo, popularity)
TRACKS = [
    ("seed-track-01", "Electric Dawn", 0, [0, 2], 0.72, 0.85, 0.60, 0.15, 138.0, 80),
    ("seed-track-02", "Fading Light", 0, [0], 0.45, 0.55, 0.40, 0.50, 98.0, 75),
    ("seed-track-03", "Starlit Road", 1, [1, 2], 0.80, 0.70, 0.85, 0.10, 122.0, 78),
    ("seed-track-04", "Ocean Floor", 1, [1], 0.60, 0.40, 0.55, 0.65, 88.0, 70),
    ("seed-track-05", "Voltage", 2, [2], 0.65, 0.92, 0.78, 0.05, 140.0, 72),
    ("seed-track-06", "Midnight Drive", 2, [2, 3], 0.50, 0.80, 0.70, 0.08, 128.0, 66),
    ("seed-track-07", "Steel Horizon", 3, [0, 8], 0.35, 0.90, 0.45, 0.12, 155.0, 73),
    ("seed-track-08", "Desert Wind", 3, [0], 0.55, 0.75, 0.50, 0.25, 118.0, 68),
    ("seed-track-09", "Silk Road", 4, [4, 6], 0.70, 0.35, 0.30, 0.80, 95.0, 62),
    ("seed-track-10", "Bloom", 4, [1], 0.85, 0.50, 0.65, 0.55, 105.0, 60),
    ("seed-track-11", "Northern Lights", 5, [0, 2], 0.68, 0.82, 0.58, 0.18, 132.0, 79),
    ("seed-track-12", "Frozen Lake", 5, [5], 0.30, 0.25, 0.20, 0.90, 72.0, 70),
    ("seed-track-13", "Wanderer", 6, [9, 7], 0.75, 0.45, 0.35, 0.70, 100.0, 58),
    ("seed-track-14", "Tidal Force", 6, [0], 0.58, 0.78, 0.52, 0.22, 126.0, 64),
    ("seed-track-15", "Ember", 7, [1, 6], 0.82, 0.60, 0.80, 0.30, 112.0, 56),
    ("seed-track-16", "Smoke Signal", 7, [3], 0.48, 0.65, 0.72, 0.15, 108.0, 52),
    ("seed-track-17", "Pixel Rain", 8, [1, 2], 0.78, 0.88, 0.82, 0.08, 135.0, 74),
    ("seed-track-18", "Neon Dreams", 8, [2], 0.62, 0.85, 0.75, 0.06, 142.0, 70),
    ("seed-track-19", "Ghost Light", 9, [2, 4], 0.42, 0.55, 0.48, 0.45, 110.0, 60),
    ("seed-track-20", "Echo Chamber", 9, [0], 0.52, 0.72, 0.55, 0.20, 120.0, 65),
]


class Command(BaseCommand):
    help = "Seed the database with deterministic test data for local development"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing catalog data before seeding",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self.stdout.write(self.style.WARNING("Flushing existing catalog data..."))
            Track.genres.through.objects.all().delete()
            Track.objects.all().delete()
            Artist.objects.all().delete()
            Genre.objects.all().delete()

        # Genres
        genre_objs = []
        for name in GENRES:
            obj, _ = Genre.objects.get_or_create(name=name)
            genre_objs.append(obj)
        self.stdout.write(f"  Genres: {len(genre_objs)}")

        # Artists
        artist_objs = []
        for aid, name, pop, country, atype, year in ARTISTS:
            obj, _ = Artist.objects.update_or_create(
                id=aid,
                defaults={
                    "name": name,
                    "popularity": pop,
                    "origin_country": country,
                    "artist_type": atype,
                    "formed_year": year,
                },
            )
            artist_objs.append(obj)
        self.stdout.write(f"  Artists: {len(artist_objs)}")

        # Tracks
        track_count = 0
        for tid, title, artist_idx, genre_idxs, val, eng, dan, aco, tmp, pop in TRACKS:
            track, _ = Track.objects.update_or_create(
                id=tid,
                defaults={
                    "title": title,
                    "artist": artist_objs[artist_idx],
                    "valence": val,
                    "energy": eng,
                    "danceability": dan,
                    "acousticness": aco,
                    "tempo": tmp,
                    "popularity": pop,
                    "is_audio_analyzed": True,
                },
            )
            track.genres.set([genre_objs[i] for i in genre_idxs])
            track_count += 1
        self.stdout.write(f"  Tracks: {track_count}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {len(genre_objs)} genres, "
                f"{len(artist_objs)} artists, {track_count} tracks"
            )
        )
