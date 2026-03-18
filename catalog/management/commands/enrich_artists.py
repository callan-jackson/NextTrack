"""Management command to enrich artist metadata from external APIs."""

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Artist, Genre
from catalog.external_data import (
    MusicBrainzClient,
    WikidataClient,
    enrich_artist_from_external_sources,
    ExternalDataError
)


class Command(BaseCommand):
    """Enrich artist data from MusicBrainz and Wikidata."""
    help = 'Enrich artist data from MusicBrainz and Wikidata external sources'

    def add_arguments(self, parser):
        """Define CLI arguments."""
        parser.add_argument(
            '--artist',
            type=str,
            help='Enrich a specific artist by name'
        )
        parser.add_argument(
            '--batch',
            action='store_true',
            help='Enrich multiple artists in batch mode'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Number of artists to enrich in batch mode (default: 10)'
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Test external API connections'
        )
        parser.add_argument(
            '--stats',
            action='store_true',
            help='Show enrichment statistics'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='use_async',
            help='Use Celery tasks for batch processing'
        )

    def handle(self, *args, **options):
        """Route to the appropriate handler based on arguments."""
        if options['test']:
            self._test_connections()
        elif options['stats']:
            self._show_statistics()
        elif options['artist']:
            self._enrich_single_artist(options['artist'])
        elif options['batch']:
            if options['use_async']:
                self._enrich_batch_async(options['limit'])
            else:
                self._enrich_batch_sync(options['limit'])
        else:
            self.stdout.write(self.style.WARNING(
                'No action specified. Use --help for available options.'
            ))

    def _test_connections(self):
        """Test connectivity to MusicBrainz and Wikidata APIs."""
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('EXTERNAL DATA API CONNECTION TEST'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING('Testing MusicBrainz API'))
        try:
            mb_client = MusicBrainzClient()
            results = mb_client.search_artist("Queen", limit=1)

            if results:
                artist = results[0]
                self.stdout.write(self.style.SUCCESS(f'  Connection: OK'))
                self.stdout.write(f'  Test search for "Queen":')
                self.stdout.write(f'    Name: {artist.get("name")}')
                self.stdout.write(f'    MBID: {artist.get("id")}')
                self.stdout.write(f'    Country: {artist.get("country")}')
                self.stdout.write(f'    Type: {artist.get("type")}')

                mbid = artist.get('id')
                if mbid:
                    details = mb_client.get_artist_details(mbid)
                    if details:
                        self.stdout.write(f'  Detailed info:')
                        self.stdout.write(f'    Formed: {details.get("formed_year")}')
                        self.stdout.write(f'    Tags: {", ".join(t["name"] for t in details.get("tags", [])[:5])}')
                        self.stdout.write(f'    Wikidata: {details.get("wikidata_id")}')
            else:
                self.stdout.write(self.style.WARNING('  No results returned'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Connection FAILED: {e}'))

        self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING('Testing Wikidata API'))
        try:
            wd_client = WikidataClient()
            entity = wd_client.get_entity('Q15862')

            if entity:
                self.stdout.write(self.style.SUCCESS(f'  Connection: OK'))
                self.stdout.write(f'  Test entity Q15862 (Queen):')
                self.stdout.write(f'    Label: {entity.get("label")}')
                self.stdout.write(f'    Description: {entity.get("description")}')
                self.stdout.write(f'    Formed: {entity.get("formed_year")}')
            else:
                self.stdout.write(self.style.WARNING('  Entity not found'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Connection FAILED: {e}'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('API connection test complete.'))

    def _show_statistics(self):
        """Display enrichment coverage stats."""
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('ARTIST ENRICHMENT STATISTICS'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        total_artists = Artist.objects.count()
        enriched = Artist.objects.filter(musicbrainz_id__isnull=False).count()
        with_country = Artist.objects.filter(origin_country__isnull=False).count()
        with_wikidata = Artist.objects.filter(wikidata_id__isnull=False).count()
        with_type = Artist.objects.filter(artist_type__isnull=False).count()
        with_year = Artist.objects.filter(formed_year__isnull=False).count()
        with_description = Artist.objects.filter(description__isnull=False).count()

        self.stdout.write(f'Total Artists:         {total_artists:,}')
        self.stdout.write(f'With MusicBrainz ID:   {enriched:,} ({enriched/total_artists*100:.1f}%)')
        self.stdout.write(f'With Country:          {with_country:,} ({with_country/total_artists*100:.1f}%)')
        self.stdout.write(f'With Wikidata ID:      {with_wikidata:,} ({with_wikidata/total_artists*100:.1f}%)')
        self.stdout.write(f'With Artist Type:      {with_type:,} ({with_type/total_artists*100:.1f}%)')
        self.stdout.write(f'With Formation Year:   {with_year:,} ({with_year/total_artists*100:.1f}%)')
        self.stdout.write(f'With Description:      {with_description:,} ({with_description/total_artists*100:.1f}%)')

        self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING('Artist Types'))
        types = Artist.objects.filter(
            artist_type__isnull=False
        ).values('artist_type').annotate(
            count=Count('id')
        ).order_by('-count')

        for t in types:
            self.stdout.write(f'  {t["artist_type"]}: {t["count"]:,}')

        self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING('Top Countries'))
        countries = Artist.objects.filter(
            origin_country__isnull=False
        ).values('origin_country').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        for c in countries:
            self.stdout.write(f'  {c["origin_country"]}: {c["count"]:,}')

        self.stdout.write('')

        self.stdout.write(self.style.MIGRATE_HEADING('Formation Decades'))
        decade_counts = {}
        for artist in Artist.objects.filter(formed_year__isnull=False):
            decade = artist.decade
            if decade:
                decade_counts[decade] = decade_counts.get(decade, 0) + 1

        for decade in sorted(decade_counts.keys()):
            self.stdout.write(f'  {decade}s: {decade_counts[decade]:,}')

    def _enrich_single_artist(self, artist_name):
        """Enrich one artist and display results."""
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO(f'ENRICHING ARTIST: {artist_name}'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        try:
            artist = Artist.objects.filter(name__icontains=artist_name).first()
            if artist:
                self.stdout.write(f'Found in database: {artist.name} (ID: {artist.id})')
            else:
                self.stdout.write(self.style.WARNING(f'Artist not found in database'))
                self.stdout.write('Fetching directly from external sources...')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Database error: {e}'))
            return

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Fetching from External Sources'))

        try:
            result = enrich_artist_from_external_sources(artist_name)

            if result.get('musicbrainz'):
                mb = result['musicbrainz']
                self.stdout.write(self.style.SUCCESS('MusicBrainz Data:'))
                self.stdout.write(f'  MBID: {mb.get("id")}')
                self.stdout.write(f'  Name: {mb.get("name")}')
                self.stdout.write(f'  Type: {mb.get("type")}')
                self.stdout.write(f'  Country: {mb.get("country")}')
                self.stdout.write(f'  Formed: {mb.get("formed_year")}')
                self.stdout.write(f'  Disbanded: {mb.get("disbanded_year")}')
                self.stdout.write(f'  Wikidata: {mb.get("wikidata_id")}')

                if mb.get('tags'):
                    tags = [t['name'] for t in mb['tags'][:5]]
                    self.stdout.write(f'  Tags: {", ".join(tags)}')
            else:
                self.stdout.write(self.style.WARNING('No MusicBrainz data found'))

            self.stdout.write('')

            if result.get('wikidata'):
                wd = result['wikidata']
                self.stdout.write(self.style.SUCCESS('Wikidata Data:'))
                self.stdout.write(f'  ID: {wd.get("id")}')
                self.stdout.write(f'  Label: {wd.get("label")}')
                self.stdout.write(f'  Description: {wd.get("description")}')
            else:
                self.stdout.write(self.style.WARNING('No Wikidata data found'))

            self.stdout.write('')

            if artist and result.get('combined'):
                self.stdout.write(self.style.MIGRATE_HEADING('Updating Database'))
                combined = result['combined']

                update_fields = []

                if combined.get('musicbrainz_id'):
                    artist.musicbrainz_id = combined['musicbrainz_id']
                    update_fields.append('musicbrainz_id')

                if combined.get('country'):
                    artist.origin_country = combined['country']
                    update_fields.append('origin_country')

                if combined.get('type'):
                    artist.artist_type = combined['type']
                    update_fields.append('artist_type')

                if combined.get('formed_year'):
                    artist.formed_year = combined['formed_year']
                    update_fields.append('formed_year')

                if combined.get('disbanded_year'):
                    artist.disbanded_year = combined['disbanded_year']
                    update_fields.append('disbanded_year')

                if combined.get('wikidata_id'):
                    artist.wikidata_id = combined['wikidata_id']
                    update_fields.append('wikidata_id')

                if combined.get('description'):
                    artist.description = combined['description']
                    update_fields.append('description')

                from django.utils import timezone
                artist.enriched_at = timezone.now()
                update_fields.append('enriched_at')

                artist.save(update_fields=update_fields)

                self.stdout.write(self.style.SUCCESS(
                    f'Updated {len(update_fields)} fields for {artist.name}'
                ))

                if result['musicbrainz'] and result['musicbrainz'].get('tags'):
                    for tag_data in result['musicbrainz']['tags'][:5]:
                        tag_name = tag_data.get('name', '').lower().strip()
                        if tag_name:
                            genre, _ = Genre.objects.get_or_create(name=tag_name)
                            for track in artist.tracks.all():
                                track.genres.add(genre)
                    self.stdout.write(f'Added genre tags to {artist.tracks.count()} tracks')

        except ExternalDataError as e:
            self.stdout.write(self.style.ERROR(f'External API error: {e}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Unexpected error: {e}'))

    def _enrich_batch_sync(self, limit):
        """Synchronous batch enrichment for small batches."""
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO(f'BATCH ENRICHMENT (sync, limit={limit})'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        artists = Artist.objects.filter(
            musicbrainz_id__isnull=True
        )[:limit]

        total = artists.count()
        self.stdout.write(f'Found {total} artists to enrich')
        self.stdout.write('')

        success = 0
        failed = 0

        for i, artist in enumerate(artists, 1):
            self.stdout.write(f'[{i}/{total}] {artist.name}... ', ending='')

            try:
                result = enrich_artist_from_external_sources(artist.name)

                if result.get('combined') and result['combined'].get('musicbrainz_id'):
                    combined = result['combined']

                    if combined.get('musicbrainz_id'):
                        artist.musicbrainz_id = combined['musicbrainz_id']
                    if combined.get('country'):
                        artist.origin_country = combined['country']
                    if combined.get('type'):
                        artist.artist_type = combined['type']
                    if combined.get('formed_year'):
                        artist.formed_year = combined['formed_year']
                    if combined.get('wikidata_id'):
                        artist.wikidata_id = combined['wikidata_id']
                    if combined.get('description'):
                        artist.description = combined['description']

                    from django.utils import timezone
                    artist.enriched_at = timezone.now()
                    artist.save()

                    self.stdout.write(self.style.SUCCESS(
                        f'{combined.get("country", "?")} | {combined.get("type", "?")}'
                    ))
                    success += 1
                else:
                    self.stdout.write(self.style.WARNING('No data'))
                    failed += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error: {e}'))
                failed += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Complete: {success} enriched, {failed} failed'))

    def _enrich_batch_async(self, limit):
        """Queue batch enrichment as a Celery task."""
        from catalog.tasks import enrich_artists_batch

        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO(f'BATCH ENRICHMENT (async, limit={limit})'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        try:
            result = enrich_artists_batch.delay(limit=limit)
            self.stdout.write(self.style.SUCCESS(f'Task queued: {result.id}'))
            self.stdout.write('Check Celery worker logs for progress.')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to queue task: {e}'))
