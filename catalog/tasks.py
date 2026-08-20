"""Celery background tasks for cache warming and data harvesting."""

import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.core.cache import cache
from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def generate_recommendations_task(self, track_id, limit=10):
    """Pre-compute and cache recommendations for a track."""
    from catalog.models import Track
    from catalog.serializers import TrackSerializer
    from catalog.services import calculate_similarity

    try:
        source_track = Track.objects.select_related('artist').prefetch_related('genres').get(id=track_id)

        recommendations = calculate_similarity(track_id, limit=limit)

        response_data = {
            'source_track': TrackSerializer(source_track).data,
            'recommendations': TrackSerializer(recommendations, many=True).data,
            'count': len(recommendations),
        }

        cache_key = f"rec_{track_id}_{limit}"
        cache.set(cache_key, response_data, timeout=86400)  # 24 hours

        logger.info(f"Generated and cached recommendations for track {track_id}")

        return {
            'status': 'success',
            'track_id': track_id,
            'cache_key': cache_key,
            'recommendations_count': len(recommendations),
        }

    except Track.DoesNotExist:
        logger.error(f"Track {track_id} not found")
        return {'status': 'error', 'message': f'Track {track_id} not found'}

    except Exception as exc:
        logger.error(f"Error generating recommendations for {track_id}: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task
def warm_cache_for_popular_tracks(popularity_threshold=70, limit=10):
    """Queue recommendation generation for all popular tracks."""
    from catalog.models import Track

    popular_tracks = Track.objects.filter(
        popularity__gte=popularity_threshold
    ).values_list('id', flat=True)[:500]

    total = len(popular_tracks)
    logger.info(f"Warming cache for {total} popular tracks")

    success_count = 0
    for track_id in popular_tracks:
        try:
            generate_recommendations_task.delay(track_id, limit)
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to queue task for track {track_id}: {e}")

    return {
        'status': 'complete',
        'tracks_queued': success_count,
        'total_popular_tracks': total,
    }


@shared_task
def clear_recommendation_cache():
    """Delete all cached recommendations."""
    from django.core.cache import cache

    try:
        cache.delete_pattern("rec_*")
        logger.info("Cleared all recommendation caches")
        return {'status': 'success', 'message': 'Cache cleared'}
    except AttributeError:
        # some backends don't support pattern deletion
        logger.warning("Cache backend doesn't support pattern deletion")
        return {'status': 'warning', 'message': 'Pattern deletion not supported'}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def enrich_artist_data_task(self, artist_id):
    """Fetch and store external metadata for an artist."""
    from catalog.external_data import ExternalDataError, MusicBrainzClient, RateLimitError, WikidataClient
    from catalog.models import Artist, Genre

    try:
        artist = Artist.objects.get(id=artist_id)

        if artist.is_enriched and artist.enriched_at:
            logger.info(f"Artist {artist.name} already enriched at {artist.enriched_at}")
            return {
                'status': 'skipped',
                'artist_id': artist_id,
                'reason': 'Already enriched',
                'musicbrainz_id': artist.musicbrainz_id
            }

        logger.info(f"Enriching artist: {artist.name}")

        mb_client = MusicBrainzClient()
        mb_results = mb_client.search_artist(artist.name, limit=3)

        if not mb_results:
            logger.info(f"No MusicBrainz results for artist {artist.name}")
            return {
                'status': 'not_found',
                'artist_id': artist_id,
                'artist_name': artist.name,
                'source': 'musicbrainz'
            }

        best_match = mb_results[0]
        mbid = best_match.get('id')

        if not mbid:
            return {
                'status': 'not_found',
                'artist_id': artist_id,
                'message': 'No MusicBrainz ID found'
            }

        mb_details = mb_client.get_artist_details(mbid)

        if not mb_details:
            artist.musicbrainz_id = mbid
            artist.enriched_at = timezone.now()
            artist.save(update_fields=['musicbrainz_id', 'enriched_at'])
            return {
                'status': 'partial',
                'artist_id': artist_id,
                'message': 'Got MBID but details fetch failed'
            }

        update_fields = ['enriched_at']

        artist.musicbrainz_id = mbid
        update_fields.append('musicbrainz_id')

        if mb_details.get('country'):
            artist.origin_country = mb_details['country']
            update_fields.append('origin_country')

        if mb_details.get('type'):
            artist.artist_type = mb_details['type']
            update_fields.append('artist_type')

        if mb_details.get('formed_year'):
            artist.formed_year = mb_details['formed_year']
            update_fields.append('formed_year')

        if mb_details.get('disbanded_year'):
            artist.disbanded_year = mb_details['disbanded_year']
            update_fields.append('disbanded_year')

        if mb_details.get('wikidata_id'):
            artist.wikidata_id = mb_details['wikidata_id']
            update_fields.append('wikidata_id')

        # fetch wikidata if available
        if artist.wikidata_id:
            try:
                wd_client = WikidataClient()
                wd_data = wd_client.get_entity(artist.wikidata_id)

                if wd_data and wd_data.get('description'):
                    artist.description = wd_data['description']
                    update_fields.append('description')

                    if not artist.formed_year and wd_data.get('formed_year'):
                        artist.formed_year = wd_data['formed_year']
                        if 'formed_year' not in update_fields:
                            update_fields.append('formed_year')

                # link artists by musical influence
                influenced_by_ids = wd_data.get('influenced_by_ids', [])
                if influenced_by_ids:
                    influence_labels = wd_client.get_entity_labels(influenced_by_ids[:10])

                    if influence_labels:
                        logger.info(
                            f"Semantic Influence: {artist.name} influenced by "
                            f"{list(influence_labels.values())}"
                        )

                        for influence_id, influence_name in influence_labels.items():
                            clean_name = ''.join(
                                c for c in influence_name if c.isalnum()
                            )
                            if clean_name:
                                tag_name = f"InfluencedBy:{clean_name}"
                                genre, _ = Genre.objects.get_or_create(name=tag_name.lower())
                                for track in artist.tracks.all():
                                    track.genres.add(genre)

            except (ExternalDataError, requests.RequestException) as e:
                logger.warning(f"Wikidata fetch failed for {artist.name}: {e}")

        # add genre tags from MusicBrainz
        if mb_details.get('tags'):
            for tag_data in mb_details['tags'][:5]:
                tag_name = tag_data.get('name', '').lower().strip()
                if tag_name and len(tag_name) <= 100:
                    genre, _ = Genre.objects.get_or_create(name=tag_name)
                    for track in artist.tracks.all():
                        track.genres.add(genre)

        artist.enriched_at = timezone.now()
        artist.save(update_fields=update_fields)

        logger.info(
            f"Enriched artist {artist.name}: "
            f"country={artist.origin_country}, "
            f"type={artist.artist_type}, "
            f"formed={artist.formed_year}"
        )

        return {
            'status': 'success',
            'artist_id': artist_id,
            'artist_name': artist.name,
            'musicbrainz_id': artist.musicbrainz_id,
            'wikidata_id': artist.wikidata_id,
            'country': artist.origin_country,
            'type': artist.artist_type,
            'formed_year': artist.formed_year,
            'description': artist.description[:100] if artist.description else None
        }

    except Artist.DoesNotExist:
        logger.error(f"Artist {artist_id} not found in database")
        return {
            'status': 'error',
            'artist_id': artist_id,
            'message': 'Artist not found'
        }

    except RateLimitError:
        logger.warning(f"Rate limited while enriching artist {artist_id}")
        raise self.retry(countdown=120)

    except ExternalDataError as exc:
        logger.error(f"External API error for artist {artist_id}: {exc}")
        raise self.retry(exc=exc)

    except Exception as exc:
        logger.error(f"Unexpected error enriching artist {artist_id}: {exc}")
        return {
            'status': 'error',
            'artist_id': artist_id,
            'message': str(exc)
        }


@shared_task
def enrich_artists_batch(limit=100, force=False):
    """Queue enrichment tasks for unenriched artists."""
    from catalog.models import Artist

    if force:
        artists_to_enrich = Artist.objects.all().values_list('id', flat=True)[:limit]
    else:
        artists_to_enrich = Artist.objects.filter(
            musicbrainz_id__isnull=True
        ).values_list('id', flat=True)[:limit]

    total = len(artists_to_enrich)
    logger.info(f"Queuing enrichment for {total} artists")

    queued = 0
    for artist_id in artists_to_enrich:
        try:
            # stagger to respect MusicBrainz rate limit
            enrich_artist_data_task.apply_async(
                args=[artist_id],
                countdown=int(queued * 1.5)
            )
            queued += 1
        except Exception as e:
            logger.error(f"Failed to queue enrichment for artist {artist_id}: {e}")

    return {
        'status': 'complete',
        'artists_queued': queued,
        'total_found': total
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def harvest_related_tracks_task(self, track_id, limit=20):
    """Fetch related tracks from Spotify and add new ones to our DB."""
    from catalog.models import Track
    from catalog.services import ingest_track_from_spotify_data
    from catalog.spotify_client import SpotifyClient, SpotifyClientError

    try:
        try:
            seed_track = Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            logger.error(f"Seed track {track_id} not found in database")
            return {
                'status': 'error',
                'message': f'Seed track {track_id} not found'
            }

        client = SpotifyClient()

        if not client.is_configured:
            logger.warning("Spotify not configured, skipping harvest")
            return {
                'status': 'skipped',
                'reason': 'Spotify not configured'
            }

        logger.info(f"Harvesting related tracks for: {seed_track.title}")

        recommendations = client.get_recommendations([track_id], limit=limit)

        if not recommendations:
            logger.info(f"No recommendations found for track {track_id}")
            return {
                'status': 'success',
                'harvested': 0,
                'reason': 'No recommendations from Spotify'
            }

        new_track_ids = []
        new_tracks_data = []

        for rec_track in recommendations:
            rec_id = rec_track.get('id')
            if not Track.objects.filter(id=rec_id).exists():
                new_track_ids.append(rec_id)
                new_tracks_data.append(rec_track)

        if not new_track_ids:
            logger.info("All recommended tracks already in database")
            return {
                'status': 'success',
                'harvested': 0,
                'reason': 'All tracks already in database'
            }

        audio_features_map = client.get_audio_features_batch(new_track_ids)
        harvested_count = 0

        for rec_track in new_tracks_data:
            rec_id = rec_track.get('id')
            features = audio_features_map.get(rec_id)

            if not features:
                continue

            try:
                track = ingest_track_from_spotify_data(rec_track, features, client)
                if track:
                    harvested_count += 1
            except (DatabaseError, SpotifyClientError, ValueError) as e:
                logger.error(f"Failed to ingest recommended track {rec_id}: {e}")
                continue

        logger.info(f"Harvested {harvested_count} new tracks related to {seed_track.title}")

        return {
            'status': 'success',
            'seed_track': seed_track.title,
            'harvested': harvested_count,
            'total_recommendations': len(recommendations)
        }

    except SpotifyClientError as e:
        logger.error(f"Spotify API error during harvest: {e}")
        raise self.retry(exc=e)

    except Exception as exc:
        logger.error(f"Unexpected error during harvest: {exc}")
        return {
            'status': 'error',
            'message': str(exc)
        }


@shared_task
def harvest_batch_from_popular_tracks(popularity_threshold=80, tracks_limit=50, recs_per_track=10):
    """Queue harvest tasks for popular tracks to expand the DB."""
    from catalog.models import Track

    popular_tracks = Track.objects.filter(
        popularity__gte=popularity_threshold
    ).values_list('id', flat=True)[:tracks_limit]

    total = len(popular_tracks)
    logger.info(f"Starting batch harvest from {total} popular tracks")

    queued = 0
    for track_id in popular_tracks:
        try:
            harvest_related_tracks_task.apply_async(
                args=[track_id, recs_per_track],
                countdown=queued * 2
            )
            queued += 1
        except Exception as e:
            logger.error(f"Failed to queue harvest for track {track_id}: {e}")

    return {
        'status': 'complete',
        'tracks_queued': queued,
        'total_seeds': total
    }


@shared_task
def ingest_track_by_spotify_id(spotify_track_id):
    """Fetch a single track from Spotify and add it to our DB."""
    from catalog.models import Track
    from catalog.services import ingest_track_from_spotify_data
    from catalog.spotify_client import SpotifyClient, SpotifyClientError

    if Track.objects.filter(id=spotify_track_id).exists():
        track = Track.objects.get(id=spotify_track_id)
        return {
            'status': 'exists',
            'track_id': spotify_track_id,
            'title': track.title,
            'artist': track.artist.name
        }

    try:
        client = SpotifyClient()

        if not client.is_configured:
            return {
                'status': 'error',
                'message': 'Spotify not configured'
            }

        track_data = client.get_track(spotify_track_id)
        if not track_data:
            return {
                'status': 'error',
                'message': 'Track not found on Spotify'
            }

        features = client.get_audio_features(spotify_track_id)
        if not features:
            return {
                'status': 'error',
                'message': 'No audio features available for this track'
            }

        track = ingest_track_from_spotify_data(track_data, features, client)

        if not track:
            return {
                'status': 'error',
                'message': 'Failed to ingest track'
            }

        logger.info(f"Ingested track by ID: {track.title} by {track.artist.name}")

        return {
            'status': 'success',
            'track_id': spotify_track_id,
            'title': track.title,
            'artist': track.artist.name
        }

    except SpotifyClientError as e:
        logger.error(f"Spotify error ingesting track {spotify_track_id}: {e}")
        return {
            'status': 'error',
            'message': str(e)
        }

    except Exception as e:
        logger.error(f"Error ingesting track {spotify_track_id}: {e}")
        return {
            'status': 'error',
            'message': str(e)
        }


@shared_task
def materialize_popular_recommendations(popularity_threshold=70, limit=50):
    """Materialize precomputed recommendations for popular tracks.

    Finds tracks whose popularity >= *popularity_threshold* (up to *limit*)
    and calls ``materialize_recommendations`` for each one so that future
    recommendation requests can be served from the precomputed table.
    """
    from catalog.models import Track
    from catalog.services import materialize_recommendations

    popular_tracks = list(
        Track.objects.filter(popularity__gte=popularity_threshold)
        .select_related('artist')
        .prefetch_related('genres')
        .order_by('-popularity')[:limit]
    )

    total = len(popular_tracks)
    logger.info(f"Materializing recommendations for {total} popular tracks")

    success_count = 0
    total_recs = 0

    for track in popular_tracks:
        try:
            count = materialize_recommendations(track, n=20)
            total_recs += count
            success_count += 1
        except Exception as e:
            logger.error(
                f"Failed to materialize recommendations for track {track.id}: {e}"
            )

    logger.info(
        f"Materialization complete: {success_count}/{total} tracks, "
        f"{total_recs} total recommendations stored"
    )

    return {
        'status': 'complete',
        'tracks_processed': success_count,
        'total_tracks': total,
        'total_recommendations': total_recs,
    }


@shared_task
def cleanup_stale_data():
    """Remove expired analytics events, old feedback, and orphaned tracks."""
    from catalog.models import AnalyticsEvent, RecommendationFeedback, Track

    now = timezone.now()

    # Delete analytics events older than 90 days
    old_events = AnalyticsEvent.objects.filter(created_at__lt=now - timedelta(days=90))
    events_count = old_events.count()
    old_events.delete()

    # Delete feedback from sessions not active in 30 days
    old_feedback = RecommendationFeedback.objects.filter(created_at__lt=now - timedelta(days=30))
    feedback_count = old_feedback.count()
    old_feedback.delete()

    # Remove tracks with no genres and no feedback (likely failed ingestions)
    orphan_tracks = Track.objects.filter(genres__isnull=True, feedback__isnull=True)
    orphan_count = orphan_tracks.count()
    orphan_tracks.delete()

    # Remove expired shared playlists
    from catalog.models import SharedPlaylist
    expired_playlists = SharedPlaylist.objects.filter(expires_at__lt=now)
    playlist_count = expired_playlists.count()
    expired_playlists.delete()

    logger.info(
        f"Cleanup complete: {events_count} old events, "
        f"{feedback_count} old feedback, {orphan_count} orphan tracks, "
        f"{playlist_count} expired playlists removed"
    )

    return {
        'events_deleted': events_count,
        'feedback_deleted': feedback_count,
        'orphan_tracks_deleted': orphan_count,
        'expired_playlists_deleted': playlist_count,
    }


@shared_task
def refresh_popular_artist_cache():
    """Proactively refresh Redis-cached external data for frequently accessed artists."""
    from django.core.cache import cache

    from catalog.external_data import get_live_external_service
    from catalog.models import AnalyticsEvent

    # Find top recommended artists from last 7 days
    seven_days_ago = timezone.now() - timedelta(days=7)

    recent_events = AnalyticsEvent.objects.filter(
        event_type='recommend',
        created_at__gte=seven_days_ago,
    ).values_list('metadata', flat=True)

    artist_counts = {}
    for metadata in recent_events:
        if metadata and isinstance(metadata, dict):
            artist_name = metadata.get('artist_name')
            if artist_name:
                artist_counts[artist_name] = artist_counts.get(artist_name, 0) + 1

    # Also get popular artists from the DB as fallback
    from catalog.models import Artist
    if len(artist_counts) < 20:
        popular_artists = Artist.objects.order_by('-popularity').values_list('name', flat=True)[:100]
        for name in popular_artists:
            if name not in artist_counts:
                artist_counts[name] = 0

    # Sort by frequency, take top 100
    top_artists = sorted(artist_counts.keys(), key=lambda x: -artist_counts[x])[:100]

    if not top_artists:
        logger.info("No artists to refresh cache for")
        return {'status': 'skipped', 'reason': 'No artists found'}

    try:
        service = get_live_external_service()
    except Exception as e:
        logger.warning(f"Could not initialize external data service: {e}")
        return {'status': 'error', 'message': str(e)}

    refreshed = 0
    for artist_name in top_artists:
        try:
            # Check if cache entry exists and has low TTL
            cache_key = f"ext_artist:v2:{artist_name.lower().strip()}"
            ttl = cache.ttl(cache_key) if hasattr(cache, 'ttl') else -1

            # Refresh if TTL < 6 hours or not cached
            if ttl < 21600 or ttl == -1:
                service.batch_get_artist_info([artist_name], max_live_fetches=1)
                refreshed += 1
        except Exception as e:
            logger.debug(f"Cache refresh failed for {artist_name}: {e}")
            continue

    logger.info(f"Refreshed external data cache for {refreshed}/{len(top_artists)} artists")
    return {'status': 'success', 'refreshed': refreshed, 'total_checked': len(top_artists)}


# ============================================================================
# AUDIO ANALYSIS
# ============================================================================
# Feature vectors are computed locally from provider preview clips. This
# replaces Spotify's /audio-features endpoint, deprecated 2024-11-27, which
# left every newly ingested track sitting on neutral 0.5 defaults and therefore
# equidistant from every centroid the recommender computes.


def _refresh_preview_url(track):
    """Fetch a current preview URL for a track.

    Deezer signs preview links with an expiry timestamp, so a URL stored at
    ingest time will eventually stop resolving. Rather than trusting the stored
    value indefinitely we refetch it from the API when analysis is due.
    """
    if not track.id.startswith('dz-'):
        return track.preview_url

    from catalog.deezer_client import get_deezer_client

    deezer_id = track.id[3:]
    payload = get_deezer_client().get_track(deezer_id)

    if not payload:
        return track.preview_url

    return payload.get('preview') or track.preview_url


def _apply_deezer_metadata(track, payload):
    """Backfill genres and release year from a Deezer track payload.

    These are skipped at ingest to keep the search path fast - one album lookup
    per search result would mean ~25 sequential API calls before the user sees
    anything - so the analysis task, already off the critical path, picks them
    up instead.
    """
    from catalog.models import Genre

    updated = []

    if not track.release_year:
        release_date = payload.get('release_date') or (payload.get('album') or {}).get('release_date')
        if release_date:
            try:
                track.release_year = int(str(release_date)[:4])
                updated.append('release_year')
            except (ValueError, IndexError):
                pass

    album_id = (payload.get('album') or {}).get('id')
    current = set(track.genres.values_list('name', flat=True))

    if album_id and current <= {'unknown'}:
        from catalog.deezer_client import get_deezer_client

        album = get_deezer_client().get_album(str(album_id))
        names = [g.get('name', '').lower().strip()[:100]
                 for g in ((album or {}).get('genres') or {}).get('data', [])]
        names = [n for n in names if n]

        if names:
            track.genres.clear()
            for name in names[:3]:
                genre, _ = Genre.objects.get_or_create(name=name)
                track.genres.add(genre)

    return updated


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def analyze_track_audio_task(self, track_id, force=False):
    """Compute audio features for one track from its preview clip."""
    from django.utils import timezone

    from catalog.audio_analysis import (
        ANALYSIS_VERSION,
        AudioAnalysisError,
        analyze_preview_url,
        is_available,
    )
    from catalog.models import Track

    if not is_available():
        logger.info("Audio analysis backend unavailable; skipping")
        return {'status': 'skipped', 'reason': 'backend unavailable'}

    try:
        track = Track.objects.get(id=track_id)
    except Track.DoesNotExist:
        logger.warning(f"Audio analysis: track {track_id} not found")
        return {'status': 'error', 'message': 'track not found'}

    if not force and track.is_audio_analyzed and track.analysis_version >= ANALYSIS_VERSION:
        return {'status': 'skipped', 'reason': 'already analysed', 'track_id': track_id}

    preview_url = track.preview_url

    try:
        if not preview_url:
            preview_url = _refresh_preview_url(track)

        if not preview_url:
            logger.info(f"No preview available for {track_id}; leaving neutral defaults")
            return {'status': 'skipped', 'reason': 'no preview', 'track_id': track_id}

        try:
            features = analyze_preview_url(preview_url)
        except AudioAnalysisError:
            # Most likely an expired signed URL - refetch once before giving up.
            refreshed = _refresh_preview_url(track)
            if not refreshed or refreshed == preview_url:
                raise
            logger.info(f"Preview URL for {track_id} was stale; retrying with a fresh one")
            preview_url = refreshed
            features = analyze_preview_url(preview_url)

    except AudioAnalysisError as exc:
        logger.warning(f"Audio analysis failed for {track_id}: {exc}")
        return {'status': 'error', 'message': str(exc), 'track_id': track_id}
    except Exception as exc:
        logger.error(f"Unexpected audio analysis error for {track_id}: {exc}")
        raise self.retry(exc=exc)

    track.valence = features['valence']
    track.energy = features['energy']
    track.danceability = features['danceability']
    track.acousticness = features['acousticness']
    track.tempo = features['tempo']
    track.loudness = features['loudness']
    track.is_audio_analyzed = True
    track.analysis_version = features['analysis_version']
    track.analyzed_at = timezone.now()
    track.preview_url = preview_url

    fields = [
        'valence', 'energy', 'danceability', 'acousticness', 'tempo', 'loudness',
        'is_audio_analyzed', 'analysis_version', 'analyzed_at', 'preview_url',
    ]

    # Only worth an extra API call when something is actually missing, which is
    # the first analysis of a track and not any later re-analysis.
    needs_metadata = not track.release_year or set(track.genres.values_list('name', flat=True)) <= {'unknown'}

    if track.id.startswith('dz-') and needs_metadata:
        try:
            from catalog.deezer_client import get_deezer_client

            payload = get_deezer_client().get_track(track.id[3:])
            if payload:
                fields.extend(_apply_deezer_metadata(track, payload))
        except Exception as exc:
            logger.debug(f"Metadata backfill skipped for {track_id}: {exc}")

    track.save(update_fields=list(dict.fromkeys(fields)))

    logger.info(
        f"Analysed {track_id}: energy={track.energy:.2f} dance={track.danceability:.2f} "
        f"acoustic={track.acousticness:.2f} valence={track.valence:.2f} tempo={track.tempo:.0f}"
    )

    return {
        'status': 'success',
        'track_id': track_id,
        'features': {k: features[k] for k in
                     ('valence', 'energy', 'danceability', 'acousticness', 'tempo', 'loudness')},
    }


@shared_task
def backfill_audio_analysis_task(limit=None):
    """Queue analysis for tracks that still carry neutral defaults.

    Safety net for anything the ingest-time queue missed: tracks ingested while
    the worker was down, tracks whose preview fetch failed, and tracks analysed
    by an older extractor version after a recalibration.
    """
    from django.conf import settings

    from catalog.audio_analysis import ANALYSIS_VERSION, is_available
    from catalog.models import Track

    if not is_available():
        return {'status': 'skipped', 'reason': 'backend unavailable'}

    if limit is None:
        limit = getattr(settings, 'AUDIO_ANALYSIS_BATCH_SIZE', 200)

    # Only tracks we can actually analyse: one that already has a preview URL,
    # or a Deezer track whose preview can be refetched. Legacy CSV rows have
    # real features and no preview, so queueing them would be pure no-op churn.
    from django.db.models import Q

    pending = Track.objects.filter(
        Q(analysis_version__lt=ANALYSIS_VERSION)
        & (Q(preview_url__isnull=False) & ~Q(preview_url='') | Q(id__startswith='dz-'))
    ).order_by('-popularity').values_list('id', flat=True)[:limit]

    track_ids = list(pending)

    for track_id in track_ids:
        analyze_track_audio_task.delay(track_id)

    logger.info(f"Queued {len(track_ids)} tracks for audio analysis backfill")
    return {'status': 'success', 'queued': len(track_ids)}
