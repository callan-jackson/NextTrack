"""Management command to report database health and statistics."""

from django.core.management.base import BaseCommand
from django.db import connection

from catalog.models import Genre, Artist, Track, RecommendationFeedback, UserSurvey, AnalyticsEvent


class Command(BaseCommand):
    help = 'Report database health statistics'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('=== NextTrack Database Health Report ===\n'))

        # Model counts
        counts = {
            'Genres': Genre.objects.count(),
            'Artists': Artist.objects.count(),
            'Tracks': Track.objects.count(),
            'Feedback': RecommendationFeedback.objects.count(),
            'Surveys': UserSurvey.objects.count(),
            'Analytics Events': AnalyticsEvent.objects.count(),
        }
        self.stdout.write(self.style.MIGRATE_HEADING('Model Counts:'))
        for name, count in counts.items():
            self.stdout.write(f'  {name}: {count}')

        # Data quality
        self.stdout.write(self.style.MIGRATE_HEADING('\nData Quality:'))
        tracks_no_genres = Track.objects.filter(genres__isnull=True).count()
        tracks_unanalyzed = Track.objects.filter(is_audio_analyzed=False).count()
        total_tracks = counts['Tracks']
        pct_unanalyzed = (tracks_unanalyzed / total_tracks * 100) if total_tracks else 0

        self.stdout.write(f'  Tracks without genres: {tracks_no_genres}')
        self.stdout.write(f'  Tracks unanalyzed: {tracks_unanalyzed} ({pct_unanalyzed:.1f}%)')

        enriched = Artist.objects.exclude(musicbrainz_id__isnull=True).count()
        total_artists = counts['Artists']
        pct_enriched = (enriched / total_artists * 100) if total_artists else 0
        self.stdout.write(f'  Artists enriched: {enriched}/{total_artists} ({pct_enriched:.1f}%)')

        # Database size (PostgreSQL only)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_database_size(current_database())')
                db_size = cursor.fetchone()[0]
                size_mb = db_size / (1024 * 1024)
                self.stdout.write(self.style.MIGRATE_HEADING(f'\nDatabase Size: {size_mb:.1f} MB'))
        except Exception:
            self.stdout.write('\nDatabase size: (not available — requires PostgreSQL)')

        self.stdout.write(self.style.SUCCESS('\nHealth check complete.'))
