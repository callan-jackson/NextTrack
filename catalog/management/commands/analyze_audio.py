"""Compute audio features for tracks from their preview clips.

Useful when there is no Celery worker running (analysis is normally queued at
ingest time), for backfilling a catalogue after enabling the feature, and for
re-analysing everything after the extractor calibration changes.

    python manage.py analyze_audio --limit 50
    python manage.py analyze_audio --track-id dz-138547415 --force
    python manage.py analyze_audio --all --force      # after ANALYSIS_VERSION bump
"""

import logging

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from catalog.audio_analysis import (
    ANALYSIS_VERSION,
    AudioAnalysisError,
    analyze_preview_url,
    is_available,
)
from catalog.models import Track

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Compute audio features for tracks from their 30-second preview clips"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=25,
                            help='Maximum tracks to analyse (default 25)')
        parser.add_argument('--track-id', type=str,
                            help='Analyse a single track by ID')
        parser.add_argument('--force', action='store_true',
                            help='Re-analyse tracks that already have features')
        parser.add_argument('--all', action='store_true',
                            help='Ignore --limit and process every eligible track')

    def handle(self, *args, **options):
        if not is_available():
            self.stderr.write(self.style.ERROR(
                "Audio analysis backend unavailable. Install the audio extras:\n"
                "  pip install librosa soundfile\n"
                "or set AUDIO_ANALYSIS_ENABLED=True if it is disabled in settings."
            ))
            return

        if options['track_id']:
            tracks = list(Track.objects.filter(id=options['track_id']))
            if not tracks:
                self.stderr.write(self.style.ERROR(f"Track {options['track_id']} not found"))
                return
        else:
            tracks = self._eligible(options['force'])
            if not options['all']:
                tracks = tracks[:options['limit']]
            tracks = list(tracks)

        if not tracks:
            self.stdout.write(self.style.WARNING("No tracks need analysis."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Analysing {len(tracks)} track(s) with extractor v{ANALYSIS_VERSION}"
        ))

        analysed = skipped = failed = 0

        for index, track in enumerate(tracks, 1):
            label = f"{track.title} - {track.artist.name}"[:58]

            if track.is_audio_analyzed and track.analysis_version >= ANALYSIS_VERSION and not options['force']:
                skipped += 1
                continue

            url = track.preview_url or self._refresh_url(track)
            if not url:
                self.stdout.write(f"  [{index}/{len(tracks)}] {label}: no preview available")
                skipped += 1
                continue

            try:
                features = analyze_preview_url(url)
            except AudioAnalysisError as exc:
                self.stdout.write(self.style.WARNING(f"  [{index}/{len(tracks)}] {label}: {exc}"))
                failed += 1
                continue

            self._save(track, features, url)
            analysed += 1

            self.stdout.write(
                f"  [{index}/{len(tracks)}] {label}: "
                f"energy={features['energy']:.2f} dance={features['danceability']:.2f} "
                f"acoustic={features['acousticness']:.2f} valence={features['valence']:.2f} "
                f"tempo={features['tempo']:.0f}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Analysed {analysed}, skipped {skipped}, failed {failed}"
        ))

    def _eligible(self, force):
        """Tracks that can be analysed, most popular first."""
        has_preview = (Q(preview_url__isnull=False) & ~Q(preview_url='')) | Q(id__startswith='dz-')
        queryset = Track.objects.filter(has_preview).select_related('artist')

        if not force:
            queryset = queryset.filter(analysis_version__lt=ANALYSIS_VERSION)

        return queryset.order_by('-popularity')

    def _refresh_url(self, track):
        if not track.id.startswith('dz-'):
            return None
        from catalog.deezer_client import get_deezer_client

        payload = get_deezer_client().get_track(track.id[3:])
        return (payload or {}).get('preview')

    def _save(self, track, features, url):
        track.valence = features['valence']
        track.energy = features['energy']
        track.danceability = features['danceability']
        track.acousticness = features['acousticness']
        track.tempo = features['tempo']
        track.loudness = features['loudness']
        track.is_audio_analyzed = True
        track.analysis_version = features['analysis_version']
        track.analyzed_at = timezone.now()
        track.preview_url = url
        track.save(update_fields=[
            'valence', 'energy', 'danceability', 'acousticness', 'tempo', 'loudness',
            'is_audio_analyzed', 'analysis_version', 'analyzed_at', 'preview_url',
        ])
