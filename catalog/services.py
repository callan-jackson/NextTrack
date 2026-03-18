"""Recommendation engine using content-based filtering with 5D audio feature vectors.

Pipeline overview
-----------------
1. **Feature extraction** -- Each track is converted to a normalised 5-D vector
   [valence, energy, danceability, acousticness, tempo/200].
2. **Centroid computation** -- The mean of all seed-playlist feature vectors
   defines the user's current taste centre.
3. **Adaptive centroid shift** -- If the session has like/dislike feedback, the
   centroid is nudged toward liked tracks and away from disliked ones using
   LIKE_WEIGHT (2.0) and DISLIKE_WEIGHT (-0.5) with a 0.1 step size.
4. **Preference blending** -- Explicit feature preferences (from the API
   request) are averaged with the adaptive centroid to produce the final target
   vector.
5. **Candidate selection with serendipity** -- 80 % of candidates come from
   genre-matched tracks; 20 % are popular tracks outside those genres to avoid
   filter bubbles.
6. **Euclidean-distance ranking** -- Candidates are ranked by L2 distance to
   the target vector; the closest ones are returned.
7. **Categorical preference re-ranking** -- Scores are boosted or penalised
   based on learned genre / country / artist-type / decade preferences from
   session feedback (see ``apply_categorical_preferences``).
8. **External-data enhancement** -- Similar-artist, influence-chain, and
   Last.fm tag-match boosts are applied via ``apply_external_data_enhancements``.

Key constants
~~~~~~~~~~~~~
* ``LIKE_WEIGHT = 2.0`` / ``DISLIKE_WEIGHT = -0.5`` -- adaptive centroid shift
  multipliers.
* ``GENRE_LIKE_BONUS = 0.12`` / ``GENRE_DISLIKE_PENALTY = 0.15`` -- per-genre
  score adjustments.
* ``SIMILAR_ARTIST_BOOST = 0.20`` -- score multiplier when the recommended
  artist appears in the similar-artist set.
* ``INFLUENCE_CHAIN_BOOST = 0.15`` -- score multiplier for influence-chain
  matches.
* ``LASTFM_TAG_MATCH_BOOST = 0.08`` -- per-tag boost (capped at 0.25 total).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
from django.db.models import Case, When, Q, QuerySet
from django.utils.text import slugify

from django.utils import timezone

from catalog.models import Track, Artist, Genre, RecommendationFeedback, PrecomputedRecommendation

logger = logging.getLogger(__name__)

FEATURE_NAMES = ['valence', 'energy', 'danceability', 'acousticness', 'tempo']

# Feedback weights for shifting the centroid
LIKE_WEIGHT = 2.0
DISLIKE_WEIGHT = -0.5


def get_feature_vector(track: Track) -> np.ndarray:
    """Convert a track into a normalized 5D feature vector."""
    return np.array([
        track.valence,
        track.energy,
        track.danceability,
        track.acousticness,
        track.tempo / 200.0  # normalize to ~0-1 like the others
    ], dtype=np.float64)


def euclidean_distance(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """Euclidean distance between two vectors. Lower = more similar."""
    return np.sqrt(np.sum((vec_a - vec_b) ** 2))


def calculate_centroid(feature_vectors: list[np.ndarray]) -> np.ndarray:
    """Average the feature vectors to get the 'center' of the user's taste."""
    if len(feature_vectors) == 0:
        return np.zeros(5, dtype=np.float64)

    matrix = np.array(feature_vectors, dtype=np.float64)
    return np.mean(matrix, axis=0)


def calculate_adaptive_centroid(base_centroid: np.ndarray, session_key: Optional[str] = None) -> tuple[np.ndarray, bool]:
    """Shift the centroid toward liked tracks and away from disliked ones."""
    if not session_key:
        return base_centroid, False

    feedback_entries = RecommendationFeedback.objects.filter(
        session_key=session_key
    ).select_related('track')

    if not feedback_entries.exists():
        return base_centroid, False

    adaptive_centroid = base_centroid.copy()
    adjustment_count = 0

    for feedback in feedback_entries:
        try:
            track_vector = get_feature_vector(feedback.track)
            direction = track_vector - base_centroid

            if feedback.score:
                adaptive_centroid += LIKE_WEIGHT * direction * 0.1
                adjustment_count += 1
            else:
                adaptive_centroid += DISLIKE_WEIGHT * direction * 0.1
                adjustment_count += 1

        except (AttributeError, TypeError, ValueError) as e:
            logger.warning(f"Skipping feedback for track {feedback.track_id}: {e}")
            continue

    adaptive_centroid = np.clip(adaptive_centroid, 0.0, 1.0)

    return adaptive_centroid, adjustment_count > 0


# --------------------------------------------------------------------------
# CATEGORICAL PREFERENCE LEARNING
# --------------------------------------------------------------------------

GENRE_LIKE_BONUS = 0.12
GENRE_DISLIKE_PENALTY = 0.15
COUNTRY_LIKE_BONUS = 0.10
COUNTRY_DISLIKE_PENALTY = 0.12
ARTIST_TYPE_LIKE_BONUS = 0.08
ARTIST_TYPE_DISLIKE_PENALTY = 0.10
DECADE_LIKE_BONUS = 0.06
DECADE_DISLIKE_PENALTY = 0.08


def calculate_categorical_preferences(session_key: Optional[str], external_data_service: Any = None) -> dict[str, Any]:
    """Build a preference profile from what the user has liked/disliked."""
    if not session_key:
        return {'has_preferences': False}

    feedback_entries = RecommendationFeedback.objects.filter(
        session_key=session_key
    ).select_related('track', 'track__artist').prefetch_related('track__genres')

    if not feedback_entries.exists():
        return {'has_preferences': False}

    genres_liked = {}
    genres_disliked = {}
    countries_liked = {}
    countries_disliked = {}
    artist_types_liked = {}
    artist_types_disliked = {}
    decades_liked = {}
    decades_disliked = {}

    for feedback in feedback_entries:
        track = feedback.track
        is_like = feedback.score

        for genre in track.genres.all():
            genre_name = genre.name.lower()
            if is_like:
                genres_liked[genre_name] = genres_liked.get(genre_name, 0) + 1
            else:
                genres_disliked[genre_name] = genres_disliked.get(genre_name, 0) + 1

        artist_name = track.artist.name
        ext_data = {}

        if external_data_service:
            batch_result = external_data_service.batch_get_artist_info([artist_name], max_live_fetches=1)
            ext_data = batch_result.get(artist_name, {})

        # Fall back to DB if external data unavailable
        country = ext_data.get('country') or track.artist.origin_country
        artist_type = ext_data.get('type') or track.artist.artist_type
        formed_year = ext_data.get('formed_year') or track.artist.formed_year

        if country:
            if is_like:
                countries_liked[country] = countries_liked.get(country, 0) + 1
            else:
                countries_disliked[country] = countries_disliked.get(country, 0) + 1

        if artist_type:
            if is_like:
                artist_types_liked[artist_type] = artist_types_liked.get(artist_type, 0) + 1
            else:
                artist_types_disliked[artist_type] = artist_types_disliked.get(artist_type, 0) + 1

        if formed_year:
            decade = f"{(formed_year // 10) * 10}s"
            if is_like:
                decades_liked[decade] = decades_liked.get(decade, 0) + 1
            else:
                decades_disliked[decade] = decades_disliked.get(decade, 0) + 1

    has_preferences = bool(
        genres_liked or genres_disliked or
        countries_liked or countries_disliked or
        artist_types_liked or artist_types_disliked or
        decades_liked or decades_disliked
    )

    return {
        'genres': {'liked': genres_liked, 'disliked': genres_disliked},
        'countries': {'liked': countries_liked, 'disliked': countries_disliked},
        'artist_types': {'liked': artist_types_liked, 'disliked': artist_types_disliked},
        'decades': {'liked': decades_liked, 'disliked': decades_disliked},
        'has_preferences': has_preferences
    }


def apply_categorical_preferences(
    recommendations: list[tuple[Track, float]],
    categorical_prefs: dict[str, Any],
    artist_info_map: dict[str, dict[str, Any]],
) -> list[tuple[Track, float, list[str]]]:
    """Boost or penalize recommendation scores based on learned category preferences."""
    if not categorical_prefs.get('has_preferences'):
        return [(track, score, []) for track, score in recommendations]

    genres_pref = categorical_prefs.get('genres', {})
    countries_pref = categorical_prefs.get('countries', {})
    artist_types_pref = categorical_prefs.get('artist_types', {})
    decades_pref = categorical_prefs.get('decades', {})

    def total_count(pref_dict):
        return sum(pref_dict.get('liked', {}).values()) + sum(pref_dict.get('disliked', {}).values())

    genre_total = max(total_count(genres_pref), 1)
    country_total = max(total_count(countries_pref), 1)
    type_total = max(total_count(artist_types_pref), 1)
    decade_total = max(total_count(decades_pref), 1)

    adjusted_recommendations = []

    for track, base_score in recommendations:
        score = base_score
        adjustments = []

        track_genres = [g.name.lower() for g in track.genres.all()]
        ext_data = artist_info_map.get(track.id, {})
        track_country = ext_data.get('country')
        track_type = ext_data.get('type')
        track_decade = ext_data.get('decade')

        for genre in track_genres:
            liked_count = genres_pref.get('liked', {}).get(genre, 0)
            disliked_count = genres_pref.get('disliked', {}).get(genre, 0)

            if liked_count > 0:
                strength = liked_count / genre_total
                bonus = GENRE_LIKE_BONUS * strength
                score = score * (1 + bonus)
                adjustments.append(f"+genre:{genre}")

            if disliked_count > 0:
                strength = disliked_count / genre_total
                penalty = GENRE_DISLIKE_PENALTY * strength
                score = score * (1 - penalty)
                adjustments.append(f"-genre:{genre}")

        if track_country:
            liked_count = countries_pref.get('liked', {}).get(track_country, 0)
            disliked_count = countries_pref.get('disliked', {}).get(track_country, 0)

            if liked_count > 0:
                strength = liked_count / country_total
                bonus = COUNTRY_LIKE_BONUS * strength
                score = score * (1 + bonus)
                adjustments.append(f"+country:{track_country}")

            if disliked_count > 0:
                strength = disliked_count / country_total
                penalty = COUNTRY_DISLIKE_PENALTY * strength
                score = score * (1 - penalty)
                adjustments.append(f"-country:{track_country}")

        if track_type:
            liked_count = artist_types_pref.get('liked', {}).get(track_type, 0)
            disliked_count = artist_types_pref.get('disliked', {}).get(track_type, 0)

            if liked_count > 0:
                strength = liked_count / type_total
                bonus = ARTIST_TYPE_LIKE_BONUS * strength
                score = score * (1 + bonus)
                adjustments.append(f"+type:{track_type}")

            if disliked_count > 0:
                strength = disliked_count / type_total
                penalty = ARTIST_TYPE_DISLIKE_PENALTY * strength
                score = score * (1 - penalty)
                adjustments.append(f"-type:{track_type}")

        if track_decade:
            liked_count = decades_pref.get('liked', {}).get(track_decade, 0)
            disliked_count = decades_pref.get('disliked', {}).get(track_decade, 0)

            if liked_count > 0:
                strength = liked_count / decade_total
                bonus = DECADE_LIKE_BONUS * strength
                score = score * (1 + bonus)
                adjustments.append(f"+decade:{track_decade}")

            if disliked_count > 0:
                strength = disliked_count / decade_total
                penalty = DECADE_DISLIKE_PENALTY * strength
                score = score * (1 - penalty)
                adjustments.append(f"-decade:{track_decade}")

        adjusted_recommendations.append((track, score, adjustments))

    adjusted_recommendations.sort(key=lambda x: x[1], reverse=True)

    return adjusted_recommendations


# --------------------------------------------------------------------------
# EXTERNAL DATA ENHANCEMENTS
# --------------------------------------------------------------------------

SIMILAR_ARTIST_BOOST = 0.20
INFLUENCE_CHAIN_BOOST = 0.15
LASTFM_TAG_MATCH_BOOST = 0.08


def apply_external_data_enhancements(
    recommendations: list[tuple[Track, float]],
    playlist_artist_names: set[str],
    external_data_service: Any = None,
) -> list[tuple[Track, float, dict[str, Any]]]:
    """Boost scores for tracks by similar/influential artists from external APIs."""
    if not external_data_service or not playlist_artist_names:
        return [(track, score, {}) for track, score in recommendations]

    similar_artists_set = set()
    influence_chain_set = set()
    playlist_tags = set()

    playlist_external = external_data_service.batch_get_artist_info(
        list(playlist_artist_names),
        max_live_fetches=3
    )

    for artist_name, ext_data in playlist_external.items():
        for similar in ext_data.get('similar_artists', []):
            similar_artists_set.add(similar.lower())
        for influenced_by in ext_data.get('influenced_by', []):
            influence_chain_set.add(influenced_by.lower())
        for tag in ext_data.get('lastfm_tags', []):
            playlist_tags.add(tag.lower())

    logger.debug(f"External enhancement data: {len(similar_artists_set)} similar artists, "
                 f"{len(influence_chain_set)} influence chain, {len(playlist_tags)} tags")

    enhanced_recommendations = []

    for track, base_score in recommendations:
        score = base_score
        enhancements = {
            'similar_artist': None,
            'influence_chain': None,
            'tag_matches': [],
            'total_boost': 0.0
        }

        rec_artist_name = track.artist.name.lower()

        if rec_artist_name in similar_artists_set:
            boost = SIMILAR_ARTIST_BOOST
            score = score * (1 + boost)
            enhancements['similar_artist'] = track.artist.name
            enhancements['total_boost'] += boost

        if rec_artist_name in influence_chain_set:
            boost = INFLUENCE_CHAIN_BOOST
            score = score * (1 + boost)
            enhancements['influence_chain'] = track.artist.name
            enhancements['total_boost'] += boost

        # Only check cached data for top candidates to avoid API spam
        if len(enhanced_recommendations) < 50:
            rec_ext = external_data_service.batch_get_artist_info(
                [track.artist.name],
                max_live_fetches=0
            ).get(track.artist.name, {})

            rec_tags = set(t.lower() for t in rec_ext.get('lastfm_tags', []))
            matching_tags = playlist_tags & rec_tags

            if matching_tags:
                tag_boost = min(len(matching_tags) * LASTFM_TAG_MATCH_BOOST, 0.25)
                score = score * (1 + tag_boost)
                enhancements['tag_matches'] = list(matching_tags)[:5]
                enhancements['total_boost'] += tag_boost

            rec_influenced_by = set(a.lower() for a in rec_ext.get('influenced_by', []))
            if playlist_artist_names & set(a.lower() for a in rec_influenced_by):
                boost = INFLUENCE_CHAIN_BOOST
                score = score * (1 + boost)
                enhancements['influence_chain'] = f"{track.artist.name} influenced by playlist"
                enhancements['total_boost'] += boost

        enhanced_recommendations.append((track, score, enhancements))

    enhanced_recommendations.sort(key=lambda x: x[1], reverse=True)

    return enhanced_recommendations


def get_influence_based_suggestions(
    playlist_artist_names: set[str],
    external_data_service: Any,
    limit: int = 5,
) -> list[str]:
    """Find artists who influenced the playlist artists for 'You might also like'."""
    if not external_data_service:
        return []

    influential_artists = set()

    external_data = external_data_service.batch_get_artist_info(
        list(playlist_artist_names),
        max_live_fetches=3
    )

    for artist_name, ext_data in external_data.items():
        for influenced_by in ext_data.get('influenced_by', []):
            if influenced_by.lower() not in {a.lower() for a in playlist_artist_names}:
                influential_artists.add(influenced_by)

    return list(influential_artists)[:limit]


def apply_preferences(centroid: np.ndarray, preferences: Optional[dict[str, float]]) -> np.ndarray:
    """Blend user's explicit feature preferences with the playlist centroid."""
    if not preferences:
        return centroid

    preference_vector = centroid.copy()

    feature_map = {
        'valence': 0,
        'energy': 1,
        'danceability': 2,
        'acousticness': 3,
        'tempo': 4
    }

    for key, value in preferences.items():
        if key in feature_map:
            idx = feature_map[key]
            if key == 'tempo':
                value = value / 200.0  # Normalize tempo
            preference_vector[idx] = max(0.0, min(1.0, float(value)))

    return (centroid + preference_vector) / 2


def get_candidates_with_serendipity(input_genre_ids: set[int], input_track_ids: set[str], serendipity_ratio: float = 0.2) -> QuerySet:
    """Mix 80% genre-matched tracks with 20% popular outliers to avoid filter bubbles."""
    total_target = 2000
    discovery_count = int(total_target * serendipity_ratio)
    genre_count = total_target - discovery_count

    if input_genre_ids:
        genre_candidates = Track.objects.filter(
            genres__id__in=list(input_genre_ids)
        ).exclude(
            id__in=input_track_ids
        ).distinct()
    else:
        genre_candidates = Track.objects.exclude(
            id__in=input_track_ids
        ).order_by('-popularity')

    genre_candidate_ids = list(genre_candidates.values_list('id', flat=True)[:genre_count])

    if input_genre_ids:
        discovery_candidates = Track.objects.filter(
            popularity__gte=70
        ).exclude(
            id__in=input_track_ids
        ).exclude(
            genres__id__in=list(input_genre_ids)
        ).distinct().order_by('-popularity')
    else:
        discovery_candidates = Track.objects.none()

    discovery_candidate_ids = list(discovery_candidates.values_list('id', flat=True)[:discovery_count])

    combined_ids = list(set(genre_candidate_ids + discovery_candidate_ids))

    return Track.objects.filter(id__in=combined_ids)


def get_recommendations_from_sequence(
    track_ids: list[str],
    preferences: Optional[dict[str, float]] = None,
    limit: int = 10,
    session_key: Optional[str] = None,
) -> dict[str, Any]:
    """Main recommendation pipeline: compute centroid, find nearest candidates, return results."""
    if not track_ids:
        return {
            'recommendations': [],
            'centroid': {},
            'input_tracks': [],
            'feedback_applied': False,
            'base_centroid': {}
        }

    input_tracks = list(
        Track.objects.filter(id__in=track_ids)
        .select_related('artist')
        .prefetch_related('genres')
    )

    if not input_tracks:
        return {
            'recommendations': [],
            'centroid': {},
            'input_tracks': [],
            'feedback_applied': False,
            'base_centroid': {}
        }

    feature_vectors = [get_feature_vector(track) for track in input_tracks]
    base_centroid = calculate_centroid(feature_vectors)

    adaptive_centroid, feedback_applied = calculate_adaptive_centroid(
        base_centroid, session_key
    )

    final_vector = apply_preferences(adaptive_centroid, preferences)

    input_genre_ids = set()
    for track in input_tracks:
        input_genre_ids.update(track.genres.values_list('id', flat=True))

    input_track_ids = set(track_ids)

    candidates = get_candidates_with_serendipity(
        input_genre_ids,
        input_track_ids,
        serendipity_ratio=0.2
    )

    candidate_data = list(candidates.values_list(
        'id', 'valence', 'energy', 'danceability', 'acousticness', 'tempo'
    ).distinct())

    if not candidate_data:
        return {
            'recommendations': [],
            'centroid': _centroid_to_dict(final_vector),
            'input_tracks': input_tracks,
            'feedback_applied': feedback_applied,
            'base_centroid': _centroid_to_dict(base_centroid)
        }

    seen_ids = {}
    unique_candidate_data = []
    for row in candidate_data:
        track_id = row[0]
        if track_id not in seen_ids:
            seen_ids[track_id] = True
            unique_candidate_data.append(row)

    candidate_data = unique_candidate_data
    candidate_ids = [row[0] for row in candidate_data]

    candidate_matrix = np.array([
        [row[1], row[2], row[3], row[4], row[5] / 200.0]
        for row in candidate_data
    ], dtype=np.float64)

    distances = np.sqrt(np.sum((candidate_matrix - final_vector) ** 2, axis=1))
    sorted_indices = np.argsort(distances)[:limit]
    recommended_ids = [candidate_ids[i] for i in sorted_indices]
    unique_recommended_ids = list(dict.fromkeys(recommended_ids))

    # Keep DB results in distance order
    preserved_order = Case(*[When(id=pk, then=pos) for pos, pk in enumerate(unique_recommended_ids)])

    recommendations = list(
        Track.objects.filter(id__in=unique_recommended_ids)
        .select_related('artist')
        .prefetch_related('genres')
        .order_by(preserved_order)
    )

    # Dedup by title+artist so remasters don't show up twice
    seen_track_ids = set()
    seen_title_artist = set()
    unique_recommendations = []

    for track in recommendations:
        title_artist_key = (track.title.lower().strip(), track.artist.name.lower().strip())

        if track.id in seen_track_ids or title_artist_key in seen_title_artist:
            continue

        seen_track_ids.add(track.id)
        seen_title_artist.add(title_artist_key)
        unique_recommendations.append(track)

    return {
        'recommendations': unique_recommendations,
        'centroid': _centroid_to_dict(final_vector),
        'input_tracks': input_tracks,
        'feedback_applied': feedback_applied,
        'base_centroid': _centroid_to_dict(base_centroid)
    }


def _centroid_to_dict(centroid: np.ndarray) -> dict[str, float]:
    """Convert centroid vector to a dict, denormalizing tempo back to BPM."""
    return {
        'valence': float(centroid[0]),
        'energy': float(centroid[1]),
        'danceability': float(centroid[2]),
        'acousticness': float(centroid[3]),
        'tempo': float(centroid[4] * 200.0)
    }


def centroid_to_vector(centroid_dict: dict[str, float]) -> np.ndarray:
    """Convert a centroid dict back to a NumPy vector."""
    return np.array([
        centroid_dict.get('valence', 0),
        centroid_dict.get('energy', 0),
        centroid_dict.get('danceability', 0),
        centroid_dict.get('acousticness', 0),
        centroid_dict.get('tempo', 0) / 200.0
    ], dtype=np.float64)


def calculate_similarity(track_id: str, limit: int = 10) -> list[Track]:
    """Find similar tracks to a single seed track."""
    result = get_recommendations_from_sequence([track_id], limit=limit)
    return result['recommendations']


# --------------------------------------------------------------------------
# MATERIALIZED / PRECOMPUTED RECOMMENDATIONS
# --------------------------------------------------------------------------

PRECOMPUTED_MAX_AGE_HOURS = 24


def materialize_recommendations(track: Track, n: int = 20) -> int:
    """Compute recommendations for *track* and store them in PrecomputedRecommendation.

    Uses an update-or-create pattern via bulk operations:
    * Existing rows for the source track are deleted.
    * Fresh rows are bulk-created.

    Returns the number of recommendations stored.
    """
    from datetime import timedelta

    recommendations = calculate_similarity(track.id, limit=n)

    if not recommendations:
        return 0

    # Build feature vector for the source track
    source_vector = get_feature_vector(track)

    rows = []
    for rec_track in recommendations:
        rec_vector = get_feature_vector(rec_track)
        dist = float(euclidean_distance(source_vector, rec_vector))
        rows.append(
            PrecomputedRecommendation(
                source_track=track,
                recommended_track=rec_track,
                distance=dist,
            )
        )

    # Delete old rows for this source track and bulk-create new ones
    PrecomputedRecommendation.objects.filter(source_track=track).delete()
    PrecomputedRecommendation.objects.bulk_create(rows)

    logger.info(
        f"Materialized {len(rows)} recommendations for track {track.id}"
    )
    return len(rows)


def get_recommendations(track_id: str, limit: int = 10) -> list[Track]:
    """Return recommendations for a single track, preferring precomputed results.

    If precomputed recommendations exist and were computed within the last
    24 hours, return them directly.  Otherwise fall back to live computation
    via ``calculate_similarity``.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(hours=PRECOMPUTED_MAX_AGE_HOURS)

    precomputed = list(
        PrecomputedRecommendation.objects.filter(
            source_track_id=track_id,
            computed_at__gte=cutoff,
        )
        .select_related('recommended_track', 'recommended_track__artist')
        .order_by('distance')[:limit]
    )

    if precomputed:
        logger.info(
            f"Serving {len(precomputed)} precomputed recommendations for {track_id}"
        )
        return [pc.recommended_track for pc in precomputed]

    # Fall back to live computation
    return calculate_similarity(track_id, limit=limit)


def generate_mood_journey(
    start_features: dict[str, float],
    end_features: dict[str, float],
    steps: int = 8,
    genre_ids: Optional[set[int]] = None,
) -> list[dict[str, Any]]:
    """Generate a playlist that transitions from start mood to end mood.

    Uses linear interpolation across the 5D feature space to create
    intermediate target vectors, then finds the closest track to each.
    """
    start_vec = np.array([
        start_features.get('valence', 0.5),
        start_features.get('energy', 0.5),
        start_features.get('danceability', 0.5),
        start_features.get('acousticness', 0.5),
        start_features.get('tempo', 120.0) / 200.0,
    ], dtype=np.float64)

    end_vec = np.array([
        end_features.get('valence', 0.5),
        end_features.get('energy', 0.5),
        end_features.get('danceability', 0.5),
        end_features.get('acousticness', 0.5),
        end_features.get('tempo', 120.0) / 200.0,
    ], dtype=np.float64)

    waypoints = [
        start_vec + (end_vec - start_vec) * (i / max(steps - 1, 1))
        for i in range(steps)
    ]

    candidates = Track.objects.select_related('artist').prefetch_related('genres')
    if genre_ids:
        candidates = candidates.filter(genres__id__in=list(genre_ids)).distinct()

    candidate_data = list(candidates.values_list(
        'id', 'valence', 'energy', 'danceability', 'acousticness', 'tempo'
    )[:2000])

    if not candidate_data:
        return []

    candidate_ids = [row[0] for row in candidate_data]
    candidate_matrix = np.array([
        [row[1], row[2], row[3], row[4], row[5] / 200.0]
        for row in candidate_data
    ], dtype=np.float64)

    journey = []
    used_ids = set()
    used_artists = {}

    for step_idx, waypoint in enumerate(waypoints):
        distances = np.sqrt(np.sum((candidate_matrix - waypoint) ** 2, axis=1))
        sorted_indices = np.argsort(distances)

        for idx in sorted_indices:
            cid = candidate_ids[idx]
            if cid in used_ids:
                continue

            track = Track.objects.select_related('artist').get(pk=cid)
            artist_count = used_artists.get(track.artist_id, 0)
            if artist_count >= 2:
                continue

            used_ids.add(cid)
            used_artists[track.artist_id] = artist_count + 1

            journey.append({
                'step': step_idx + 1,
                'track': track,
                'target': {
                    'valence': round(float(waypoint[0]), 3),
                    'energy': round(float(waypoint[1]), 3),
                    'danceability': round(float(waypoint[2]), 3),
                    'acousticness': round(float(waypoint[3]), 3),
                    'tempo': round(float(waypoint[4] * 200), 1),
                },
                'distance': round(float(distances[idx]), 4),
            })
            break

    return journey


def random_walk_recommendations(
    seed_id: str,
    walk_length: int = 5,
    serendipity: float = 0.5,
) -> list[dict[str, Any]]:
    """Generate a discovery chain: each track seeds the next recommendation."""
    try:
        seed = Track.objects.select_related('artist').get(pk=seed_id)
    except Track.DoesNotExist:
        return []

    candidates = list(Track.objects.exclude(pk=seed_id).values_list(
        'id', 'valence', 'energy', 'danceability', 'acousticness', 'tempo'
    )[:3000])

    if not candidates:
        return []

    candidate_ids = [r[0] for r in candidates]
    candidate_matrix = np.array([
        [r[1], r[2], r[3], r[4], r[5] / 200.0] for r in candidates
    ], dtype=np.float64)

    current_vec = get_feature_vector(seed)
    walk = [{'step': 0, 'track': seed, 'distance': 0.0, 'shift': {}}]
    used_ids = {seed_id}

    for step in range(1, walk_length + 1):
        # Add noise for serendipity
        noise = np.random.normal(0, serendipity * 0.15, size=5)
        target = np.clip(current_vec + noise, 0.0, 1.0)

        distances = np.sqrt(np.sum((candidate_matrix - target) ** 2, axis=1))

        # Weighted random pick from top candidates
        sorted_idx = np.argsort(distances)
        top_k = min(10, len(sorted_idx))
        top_indices = sorted_idx[:top_k]

        weights = 1.0 / (distances[top_indices] + 0.01)
        weights /= weights.sum()

        chosen_local = np.random.choice(top_k, p=weights)
        chosen_idx = top_indices[chosen_local]
        chosen_id = candidate_ids[chosen_idx]

        if chosen_id in used_ids:
            # Fallback: pick next unused
            for idx in sorted_idx:
                if candidate_ids[idx] not in used_ids:
                    chosen_idx = idx
                    chosen_id = candidate_ids[idx]
                    break

        used_ids.add(chosen_id)
        new_vec = candidate_matrix[chosen_idx]

        shift = {}
        features = ['valence', 'energy', 'danceability', 'acousticness', 'tempo']
        for i, feat in enumerate(features):
            diff = float(new_vec[i] - current_vec[i])
            if abs(diff) > 0.05:
                shift[feat] = round(diff, 3)

        track = Track.objects.select_related('artist').get(pk=chosen_id)
        walk.append({
            'step': step,
            'track': track,
            'distance': round(float(distances[chosen_idx]), 4),
            'shift': shift,
        })

        current_vec = new_vec

    return walk


ACTIVITY_PRESETS = {
    'running': [
        {'name': 'Warm-up', 'tempo': (100, 120), 'energy': (0.4, 0.6), 'tracks': 2},
        {'name': 'Peak', 'tempo': (140, 170), 'energy': (0.8, 1.0), 'tracks': 4},
        {'name': 'Cool-down', 'tempo': (90, 110), 'energy': (0.3, 0.5), 'tracks': 2},
    ],
    'study': [
        {'name': 'Focus', 'tempo': (60, 100), 'energy': (0.1, 0.4), 'acousticness': (0.6, 1.0), 'tracks': 8},
    ],
    'party': [
        {'name': 'Warm-up', 'tempo': (100, 120), 'energy': (0.5, 0.7), 'danceability': (0.5, 0.7), 'tracks': 2},
        {'name': 'Peak', 'tempo': (120, 140), 'energy': (0.7, 1.0), 'danceability': (0.7, 1.0), 'tracks': 5},
        {'name': 'Wind-down', 'tempo': (90, 115), 'energy': (0.3, 0.6), 'tracks': 2},
    ],
    'yoga': [
        {'name': 'Centering', 'tempo': (60, 80), 'energy': (0.1, 0.3), 'acousticness': (0.7, 1.0), 'tracks': 3},
        {'name': 'Flow', 'tempo': (70, 95), 'energy': (0.2, 0.5), 'acousticness': (0.5, 0.9), 'tracks': 4},
        {'name': 'Savasana', 'tempo': (50, 75), 'energy': (0.05, 0.2), 'acousticness': (0.8, 1.0), 'tracks': 2},
    ],
}


def generate_activity_playlist(
    activity: str,
    seed_track_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Generate a playlist tailored to an activity structure (warm-up, peak, cool-down)."""
    phases = ACTIVITY_PRESETS.get(activity)
    if not phases:
        return {'error': f'Unknown activity: {activity}. Available: {list(ACTIVITY_PRESETS.keys())}'}

    used_ids = set(seed_track_ids or [])
    result_phases = []

    for phase in phases:
        qs = Track.objects.select_related('artist').exclude(id__in=used_ids)

        tempo_min, tempo_max = phase.get('tempo', (0, 300))
        energy_min, energy_max = phase.get('energy', (0, 1))
        qs = qs.filter(tempo__gte=tempo_min, tempo__lte=tempo_max, energy__gte=energy_min, energy__lte=energy_max)

        if 'acousticness' in phase:
            a_min, a_max = phase['acousticness']
            qs = qs.filter(acousticness__gte=a_min, acousticness__lte=a_max)
        if 'danceability' in phase:
            d_min, d_max = phase['danceability']
            qs = qs.filter(danceability__gte=d_min, danceability__lte=d_max)

        tracks = list(qs.order_by('-popularity')[:phase['tracks'] * 3])

        # Pick top tracks, avoiding duplicate artists
        selected = []
        used_artists = set()
        for t in tracks:
            if t.artist_id not in used_artists and t.id not in used_ids:
                selected.append(t)
                used_ids.add(t.id)
                used_artists.add(t.artist_id)
                if len(selected) >= phase['tracks']:
                    break

        result_phases.append({
            'name': phase['name'],
            'tracks': selected,
            'target': {
                'tempo': f"{tempo_min}-{tempo_max} BPM",
                'energy': f"{energy_min}-{energy_max}",
            },
        })

    return {
        'activity': activity,
        'phases': result_phases,
        'total_tracks': sum(len(p['tracks']) for p in result_phases),
    }


# --------------------------------------------------------------------------
# HYBRID SEARCH
# --------------------------------------------------------------------------

def search_tracks(query: str, limit: int = 20) -> list[Track]:
    """Search local DB + Spotify, deduplicate, and rank by relevance."""
    if not query or len(query) < 2:
        return []

    raw_local_results = Track.objects.filter(
        Q(title__icontains=query) |
        Q(artist__name__icontains=query) |
        Q(genres__name__icontains=query)
    ).select_related('artist').distinct()

    local_results = list(raw_local_results)

    seen_keys = set()
    for track in local_results:
        key = (track.title.lower().strip(), track.artist.name.lower().strip())
        seen_keys.add(key)

    logger.info(f"Local search for '{query}' found {len(local_results)} matches")

    spotify_new_tracks = _fetch_and_ingest_from_spotify(
        query,
        limit=limit,
        existing_keys=seen_keys
    )

    combined_results = local_results + spotify_new_tracks

    # Prioritize artist matches over genre matches to reduce noise
    query_lower = query.lower().strip()

    def smart_sort_key(track):
        """Sort key: artist match > title match > genre match > popularity."""
        artist_name = track.artist.name.lower()
        title_name = track.title.lower()

        is_exact_artist = artist_name == query_lower
        is_startswith_artist = artist_name.startswith(query_lower)
        is_exact_title = title_name == query_lower
        is_partial_artist = query_lower in artist_name
        is_partial_title = query_lower in title_name

        # not True = 0 (sorts first), not False = 1 (sorts later)
        return (
            not is_exact_artist,
            not is_startswith_artist,
            not is_exact_title,
            not is_partial_artist,
            not is_partial_title,
            -track.popularity
        )

    combined_results.sort(key=smart_sort_key)

    # Deduplicate by title + artist (same song can have multiple Spotify IDs)
    seen_dedup = set()
    unique_results = []
    for track in combined_results:
        key = (track.title.lower().strip(), track.artist.name.lower().strip())
        if key not in seen_dedup:
            seen_dedup.add(key)
            unique_results.append(track)
    combined_results = unique_results

    logger.info(
        f"Hybrid search for '{query}': {len(local_results)} local + "
        f"{len(spotify_new_tracks)} new from Spotify = {len(combined_results)} total"
    )

    return combined_results[:limit]


def _fetch_and_ingest_from_spotify(query, limit=20, existing_keys=None):
    """Search Spotify and save new tracks to local DB. Uses neutral defaults if audio features unavailable."""
    from catalog.spotify_client import SpotifyClient, SpotifyClientError

    if existing_keys is None:
        existing_keys = set()

    try:
        client = SpotifyClient()

        if not client.is_configured:
            logger.warning("Spotify not configured, skipping API search")
            return []

        spotify_results = client.search_tracks(query, limit=limit)

        if not spotify_results:
            return []

        new_tracks_data = []
        track_ids_to_fetch = []

        for sp_track in spotify_results:
            track_id = sp_track.get('id')
            track_name = sp_track.get('name', 'Unknown')
            artists = sp_track.get('artists', [])
            artist_name = artists[0].get('name', 'Unknown') if artists else 'Unknown'

            if Track.objects.filter(id=track_id).exists():
                continue

            title_artist_key = (track_name.lower().strip(), artist_name.lower().strip())
            if title_artist_key in existing_keys:
                continue

            new_tracks_data.append({
                'spotify_data': sp_track,
                'track_id': track_id,
                'title_artist_key': title_artist_key
            })
            track_ids_to_fetch.append(track_id)

        if not track_ids_to_fetch:
            logger.info("All Spotify results already in database")
            return []

        audio_features_map = client.get_audio_features_batch(track_ids_to_fetch)

        audio_features_available = bool(audio_features_map)
        if not audio_features_available:
            logger.warning("Audio Features API returned no data, using neutral defaults")

        ingested_tracks = []

        for track_data in new_tracks_data:
            track_id = track_data['track_id']
            sp_track = track_data['spotify_data']

            features = audio_features_map.get(track_id) if audio_features_available else None

            try:
                track = _ingest_spotify_track(sp_track, features, client)
                if track:
                    ingested_tracks.append(track)
                    existing_keys.add(track_data['title_artist_key'])
            except Exception as e:
                logger.error(f"Failed to ingest track {track_id}: {e}")
                continue

        analyzed_count = sum(1 for t in ingested_tracks if t.is_audio_analyzed)
        unanalyzed_count = len(ingested_tracks) - analyzed_count

        logger.info(
            f"Ingested {len(ingested_tracks)} tracks from Spotify "
            f"({analyzed_count} with features, {unanalyzed_count} with defaults)"
        )
        return ingested_tracks

    except SpotifyClientError as e:
        logger.error(f"Spotify API error during search: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error during Spotify ingestion: {e}")
        return []


def ingest_track_from_spotify_data(
    spotify_track_data: dict[str, Any],
    audio_features: Optional[dict[str, Any]],
    spotify_client: Any,
) -> Optional[Track]:
    """Create a Track record from Spotify API data.

    This is the single entry point for ingesting a Spotify track into the DB.
    Used by search ingestion, harvest tasks, and single-track ingestion.

    Args:
        spotify_track_data: dict from the Spotify tracks API.
        audio_features: dict from the Spotify audio-features API, or None
            to use neutral defaults (is_audio_analyzed will be False).
        spotify_client: a SpotifyClient instance for fetching artist genres.

    Returns:
        The created Track, or None on failure.
    """
    try:
        track_id = spotify_track_data['id']
        track_name = spotify_track_data.get('name', 'Unknown')[:500]
        popularity = spotify_track_data.get('popularity', 0)

        artists = spotify_track_data.get('artists', [])
        if not artists:
            logger.warning(f"Track {track_id} has no artist data")
            return None

        primary_artist = artists[0]
        artist_spotify_id = primary_artist.get('id')
        artist_name = primary_artist.get('name', 'Unknown')[:255]

        artist = _get_or_create_artist(artist_spotify_id, artist_name, spotify_client)

        has_audio_features = audio_features is not None

        if has_audio_features:
            valence = max(0.0, min(1.0, float(audio_features.get('valence', 0.5))))
            energy = max(0.0, min(1.0, float(audio_features.get('energy', 0.5))))
            danceability = max(0.0, min(1.0, float(audio_features.get('danceability', 0.5))))
            acousticness = max(0.0, min(1.0, float(audio_features.get('acousticness', 0.5))))
            tempo = max(0.0, min(300.0, float(audio_features.get('tempo', 120.0))))
            loudness = float(audio_features.get('loudness', -10.0))
        else:
            valence = 0.5
            energy = 0.5
            danceability = 0.5
            acousticness = 0.5
            tempo = 120.0
            loudness = -10.0
            logger.info(f"Track '{track_name}' ingested with neutral defaults (is_audio_analyzed=False)")

        # Extract release year from album data
        album_data = spotify_track_data.get('album', {})
        release_date_str = album_data.get('release_date', '')
        release_year = None
        if release_date_str:
            try:
                release_year = int(release_date_str[:4])
            except (ValueError, IndexError):
                pass

        track = Track.objects.create(
            id=track_id,
            title=track_name,
            artist=artist,
            valence=valence,
            energy=energy,
            danceability=danceability,
            acousticness=acousticness,
            tempo=tempo,
            loudness=loudness,
            popularity=popularity,
            is_audio_analyzed=has_audio_features,
            release_year=release_year,
            artist_name=artist_name,
            artist_popularity=artist.popularity,
        )

        _assign_genres_to_track(track, artist_spotify_id, spotify_client)

        logger.debug(f"Ingested track: {track_name} by {artist_name} (analyzed={has_audio_features})")
        return track

    except Exception as e:
        logger.error(f"Error ingesting Spotify track: {e}")
        return None


# Keep backward-compatible alias used by _fetch_and_ingest_from_spotify
_ingest_spotify_track = ingest_track_from_spotify_data


def _get_or_create_artist(spotify_artist_id, artist_name, client):
    """Get or create an Artist record, using slugified name as the ID to match CSV imports."""
    artist_slug = slugify(artist_name)
    if not artist_slug:
        artist_slug = spotify_artist_id or f"artist-{artist_name[:20]}"

    artist_id = artist_slug[:100]

    try:
        return Artist.objects.get(id=artist_id)
    except Artist.DoesNotExist:
        pass

    try:
        return Artist.objects.get(name=artist_name)
    except Artist.DoesNotExist:
        pass

    artist = Artist.objects.create(
        id=artist_id,
        name=artist_name,
        popularity=0
    )

    logger.debug(f"Created new artist: {artist_name} (ID: {artist_id})")
    return artist


def _assign_genres_to_track(track, artist_spotify_id, client):
    """Pull genres from the artist's Spotify profile since Spotify doesn't tag tracks directly."""
    if not artist_spotify_id:
        default_genre, _ = Genre.objects.get_or_create(name='unknown')
        track.genres.add(default_genre)
        return

    try:
        artist_data = client.get_artist(artist_spotify_id)

        if not artist_data:
            default_genre, _ = Genre.objects.get_or_create(name='unknown')
            track.genres.add(default_genre)
            return

        genres = artist_data.get('genres', [])

        if not genres:
            default_genre, _ = Genre.objects.get_or_create(name='unknown')
            track.genres.add(default_genre)
            return

        for genre_name in genres[:3]:
            genre_name = genre_name.lower().strip()[:100]
            genre, _ = Genre.objects.get_or_create(name=genre_name)
            track.genres.add(genre)

        logger.debug(f"Assigned {len(genres[:3])} genres to track {track.id}")

    except Exception as e:
        logger.error(f"Error assigning genres: {e}")
        default_genre, _ = Genre.objects.get_or_create(name='unknown')
        track.genres.add(default_genre)


# ============================================================================
# ENHANCED RECOMMENDATIONS WITH FILTERS AND EXPLANATIONS
# ============================================================================

COUNTRY_NAMES = {
    'US': 'United States', 'GB': 'United Kingdom', 'CA': 'Canada',
    'AU': 'Australia', 'DE': 'Germany', 'FR': 'France', 'JP': 'Japan',
    'KR': 'South Korea', 'SE': 'Sweden', 'NO': 'Norway', 'FI': 'Finland',
    'DK': 'Denmark', 'NL': 'Netherlands', 'BE': 'Belgium', 'IT': 'Italy',
    'ES': 'Spain', 'BR': 'Brazil', 'MX': 'Mexico', 'AR': 'Argentina',
    'NZ': 'New Zealand', 'IE': 'Ireland', 'AT': 'Austria', 'CH': 'Switzerland',
    'PT': 'Portugal', 'PL': 'Poland', 'RU': 'Russia', 'UA': 'Ukraine',
    'IN': 'India', 'CN': 'China', 'TW': 'Taiwan', 'HK': 'Hong Kong',
    'SG': 'Singapore', 'ZA': 'South Africa', 'NG': 'Nigeria', 'EG': 'Egypt',
    'IL': 'Israel', 'TR': 'Turkey', 'GR': 'Greece', 'CZ': 'Czech Republic',
    'HU': 'Hungary', 'RO': 'Romania', 'CO': 'Colombia', 'CL': 'Chile',
    'PE': 'Peru', 'VE': 'Venezuela', 'PR': 'Puerto Rico', 'JM': 'Jamaica',
    'IS': 'Iceland', 'PH': 'Philippines', 'ID': 'Indonesia', 'MY': 'Malaysia',
    'TH': 'Thailand', 'VN': 'Vietnam',
}

REGION_GROUPS = {
    'north_america': ['US', 'CA', 'MX'],
    'south_america': ['BR', 'AR', 'CO', 'CL', 'PE', 'VE'],
    'western_europe': ['GB', 'FR', 'DE', 'NL', 'BE', 'AT', 'CH', 'IE'],
    'southern_europe': ['ES', 'IT', 'PT', 'GR'],
    'northern_europe': ['SE', 'NO', 'FI', 'DK', 'IS'],
    'eastern_europe': ['PL', 'CZ', 'HU', 'RO', 'UA', 'RU'],
    'east_asia': ['JP', 'KR', 'CN', 'TW', 'HK'],
    'southeast_asia': ['SG', 'PH', 'ID', 'MY', 'TH', 'VN'],
    'south_asia': ['IN'],
    'middle_east': ['IL', 'TR', 'EG'],
    'africa': ['ZA', 'NG'],
    'oceania': ['AU', 'NZ'],
    'caribbean': ['PR', 'JM'],
}


def precompute_feature_vectors(queryset):
    """Convert a Track queryset into a pandas DataFrame of normalized feature vectors for fast similarity computation."""
    import pandas as pd
    import numpy as np

    tracks = list(queryset.values('id', 'valence', 'energy', 'danceability', 'acousticness', 'tempo'))
    if not tracks:
        return pd.DataFrame()

    df = pd.DataFrame(tracks)
    # Normalize tempo to 0-1 range (matching audio features scale)
    df['tempo_norm'] = df['tempo'] / 300.0
    feature_cols = ['valence', 'energy', 'danceability', 'acousticness', 'tempo_norm']

    # Compute feature matrix for vectorized distance calculations
    df['feature_vector'] = df[feature_cols].values.tolist()

    return df


def get_enhanced_recommendations(
    track_ids: list[str],
    preferences: Optional[dict[str, float]] = None,
    limit: int = 10,
    session_key: Optional[str] = None,
    country_filter: Optional[str] = None,
    decade_filter: Optional[str] = None,
    artist_type_filter: Optional[str] = None,
    exclude_unanalyzed: bool = False,
    include_explanations: bool = True,
    min_bpm: Optional[float] = None,
    max_bpm: Optional[float] = None,
    mood: Optional[str] = None,
    popularity_tier: Optional[str] = None,
) -> dict[str, Any]:
    """Same as get_recommendations_from_sequence but with filters and 'why this track?' explanations."""
    if not track_ids:
        return {
            'recommendations': [],
            'explanations': {},
            'centroid': {},
            'input_tracks': [],
            'feedback_applied': False,
            'filters_applied': {},
            'diversity_stats': {}
        }

    input_tracks = list(
        Track.objects.filter(id__in=track_ids)
        .select_related('artist')
        .prefetch_related('genres')
    )

    if not input_tracks:
        return {
            'recommendations': [],
            'explanations': {},
            'centroid': {},
            'input_tracks': [],
            'feedback_applied': False,
            'filters_applied': {},
            'diversity_stats': {}
        }

    feature_vectors = [get_feature_vector(track) for track in input_tracks]
    base_centroid = calculate_centroid(feature_vectors)

    adaptive_centroid, feedback_applied = calculate_adaptive_centroid(
        base_centroid, session_key
    )

    final_vector = apply_preferences(adaptive_centroid, preferences)

    input_genre_ids = set()
    for track in input_tracks:
        input_genre_ids.update(track.genres.values_list('id', flat=True))

    input_track_ids = set(track_ids)

    candidates = Track.objects.exclude(id__in=input_track_ids).select_related('artist')
    filters_applied = {}

    if country_filter:
        country_filter_upper = country_filter.upper()
        if country_filter.lower() in REGION_GROUPS:
            region_countries = REGION_GROUPS[country_filter.lower()]
            candidates = candidates.filter(artist__origin_country__in=region_countries)
            filters_applied['region'] = country_filter.lower()
        else:
            candidates = candidates.filter(artist__origin_country=country_filter_upper)
            filters_applied['country'] = country_filter_upper

    if decade_filter:
        decade_str = str(decade_filter).rstrip('s')
        try:
            decade_start = int(decade_str)
        except ValueError:
            decade_start = None

        if decade_start:
            decade_end = decade_start + 9
            # Prefer track release_year, fall back to artist formed_year
            candidates = candidates.filter(
                Q(release_year__gte=decade_start, release_year__lte=decade_end) |
                Q(release_year__isnull=True, artist__formed_year__gte=decade_start, artist__formed_year__lte=decade_end)
            )
            filters_applied['decade'] = decade_start

    if artist_type_filter:
        candidates = candidates.filter(artist__artist_type__iexact=artist_type_filter)
        filters_applied['artist_type'] = artist_type_filter

    if exclude_unanalyzed:
        candidates = candidates.filter(is_audio_analyzed=True)
        filters_applied['analyzed_only'] = True

    if min_bpm:
        candidates = candidates.filter(tempo__gte=min_bpm)
        filters_applied['min_bpm'] = min_bpm

    if max_bpm:
        candidates = candidates.filter(tempo__lte=max_bpm)
        filters_applied['max_bpm'] = max_bpm

    mood_filters = {
        'happy_energetic': {'valence__gte': 0.5, 'energy__gte': 0.5},
        'happy_calm': {'valence__gte': 0.5, 'energy__lt': 0.5},
        'sad_energetic': {'valence__lt': 0.5, 'energy__gte': 0.5},
        'sad_calm': {'valence__lt': 0.5, 'energy__lt': 0.5},
    }
    if mood and mood in mood_filters:
        candidates = candidates.filter(**mood_filters[mood])
        filters_applied['mood'] = mood

    popularity_tiers = {
        'mainstream': (80, 101),
        'popular': (60, 80),
        'underground': (40, 60),
        'hidden_gems': (0, 40),
    }
    if popularity_tier and popularity_tier in popularity_tiers:
        lo, hi = popularity_tiers[popularity_tier]
        candidates = candidates.filter(popularity__gte=lo, popularity__lt=hi)
        filters_applied['popularity_tier'] = popularity_tier

    # Prefer genre-matched candidates but fall back to all if not enough
    if input_genre_ids:
        genre_matched = candidates.filter(genres__id__in=list(input_genre_ids)).distinct()
        if genre_matched.count() >= limit:
            candidates = genre_matched

    candidate_data = list(candidates.values_list(
        'id', 'valence', 'energy', 'danceability', 'acousticness', 'tempo'
    ).distinct()[:2000])

    if not candidate_data:
        return {
            'recommendations': [],
            'explanations': {},
            'centroid': _centroid_to_dict(final_vector),
            'input_tracks': input_tracks,
            'feedback_applied': feedback_applied,
            'filters_applied': filters_applied,
            'diversity_stats': {},
            'message': 'No tracks match your filters. Try broadening your criteria.'
        }

    seen_ids = {}
    unique_candidate_data = []
    for row in candidate_data:
        track_id = row[0]
        if track_id not in seen_ids:
            seen_ids[track_id] = True
            unique_candidate_data.append(row)

    candidate_data = unique_candidate_data
    candidate_ids = [row[0] for row in candidate_data]

    candidate_matrix = np.array([
        [row[1], row[2], row[3], row[4], row[5] / 200.0]
        for row in candidate_data
    ], dtype=np.float64)

    distances = np.sqrt(np.sum((candidate_matrix - final_vector) ** 2, axis=1))
    sorted_indices = np.argsort(distances)[:limit * 2]  # extra for dedup

    recommended_ids = [candidate_ids[i] for i in sorted_indices]
    distance_map = {candidate_ids[i]: distances[i] for i in sorted_indices}

    preserved_order = Case(*[When(id=pk, then=pos) for pos, pk in enumerate(recommended_ids)])
    recommendations_raw = list(
        Track.objects.filter(id__in=recommended_ids)
        .select_related('artist')
        .prefetch_related('genres')
        .order_by(preserved_order)
    )

    seen_track_ids = set()
    seen_title_artist = set()
    recommendations = []

    for track in recommendations_raw:
        title_artist_key = (track.title.lower().strip(), track.artist.name.lower().strip())
        if track.id in seen_track_ids or title_artist_key in seen_title_artist:
            continue
        seen_track_ids.add(track.id)
        seen_title_artist.add(title_artist_key)
        recommendations.append(track)
        if len(recommendations) >= limit:
            break

    explanations = {}
    if include_explanations:
        explanations = _generate_explanations(
            recommendations, input_tracks, final_vector, distance_map, filters_applied
        )

    diversity_stats = _calculate_diversity_stats(recommendations)

    return {
        'recommendations': recommendations,
        'explanations': explanations,
        'centroid': _centroid_to_dict(final_vector),
        'base_centroid': _centroid_to_dict(base_centroid),
        'input_tracks': input_tracks,
        'feedback_applied': feedback_applied,
        'filters_applied': filters_applied,
        'diversity_stats': diversity_stats
    }


def _generate_explanations(recommendations, input_tracks, target_vector, distance_map, filters_applied):
    """Build 'why this track?' explanations based on similarity, genres, country, era, etc."""
    explanations = {}

    input_genres = set()
    input_countries = set()
    input_decades = set()
    input_artists = set()

    for track in input_tracks:
        input_artists.add(track.artist.name)
        if track.artist.origin_country:
            input_countries.add(track.artist.origin_country)
        if track.artist.decade:
            input_decades.add(track.artist.decade)
        for genre in track.genres.all():
            input_genres.add(genre.name)

    for track in recommendations:
        reasons = []
        distance = distance_map.get(track.id, 0)
        if distance < 0.15:
            reasons.append("Very similar audio profile to your playlist")
        elif distance < 0.25:
            reasons.append("Similar energy and mood to your taste")
        elif distance < 0.4:
            reasons.append("Moderate audio similarity to your playlist")

        track_genres = set(g.name for g in track.genres.all())
        shared_genres = input_genres & track_genres
        if shared_genres:
            if len(shared_genres) >= 2:
                reasons.append(f"Shares genres: {', '.join(list(shared_genres)[:2])}")
            else:
                reasons.append(f"Same genre: {list(shared_genres)[0]}")

        if track.artist.origin_country and track.artist.origin_country in input_countries:
            country_name = COUNTRY_NAMES.get(track.artist.origin_country, track.artist.origin_country)
            reasons.append(f"From {country_name} like artists in your playlist")

        if track.artist.decade and track.artist.decade in input_decades:
            reasons.append(f"From the {track.artist.decade}s era you enjoy")

        if 'country' in filters_applied:
            country_name = COUNTRY_NAMES.get(filters_applied['country'], filters_applied['country'])
            reasons.append(f"Matches your {country_name} filter")
        if 'decade' in filters_applied:
            reasons.append(f"Matches your {filters_applied['decade']}s filter")
        if 'artist_type' in filters_applied:
            reasons.append(f"Is a {filters_applied['artist_type'].lower()} as requested")

        track_vector = get_feature_vector(track)
        if track_vector[1] > 0.8:
            reasons.append("High energy track")
        if track_vector[0] > 0.8:
            reasons.append("Upbeat, positive mood")
        if track_vector[2] > 0.8:
            reasons.append("Great for dancing")

        explanations[track.id] = {
            'reasons': reasons[:4],
            'distance': round(distance, 3),
            'similarity_score': round(max(0, 1 - distance) * 100, 1)
        }

    return explanations


def _calculate_diversity_stats(tracks):
    """Count how diverse the recommendations are across country, decade, genre, etc."""
    if not tracks:
        return {}

    countries = {}
    decades = {}
    artist_types = {}
    genres = {}

    for track in tracks:
        country = track.artist.origin_country
        if country:
            country_name = COUNTRY_NAMES.get(country, country)
            countries[country_name] = countries.get(country_name, 0) + 1

        decade = track.artist.decade
        if decade:
            decade_label = f"{decade}s"
            decades[decade_label] = decades.get(decade_label, 0) + 1

        artist_type = track.artist.artist_type
        if artist_type:
            artist_types[artist_type] = artist_types.get(artist_type, 0) + 1

        for genre in track.genres.all():
            genres[genre.name] = genres.get(genre.name, 0) + 1

    def sort_dist(d, limit=5):
        return dict(sorted(d.items(), key=lambda x: -x[1])[:limit])

    total = len(tracks)

    return {
        'total_tracks': total,
        'countries': sort_dist(countries),
        'country_count': len(countries),
        'decades': sort_dist(decades),
        'decade_count': len(decades),
        'artist_types': sort_dist(artist_types),
        'genres': sort_dist(genres, limit=8),
        'genre_count': len(genres),
        'diversity_score': _compute_diversity_score(countries, decades, artist_types)
    }


def _compute_diversity_score(countries, decades, artist_types):
    """0-100 score based on entropy -- higher means more diverse recommendations."""
    import math

    def entropy(dist):
        if not dist:
            return 0
        total = sum(dist.values())
        if total == 0:
            return 0
        probs = [v / total for v in dist.values()]
        return -sum(p * math.log2(p) for p in probs if p > 0)

    def normalized_entropy(dist):
        if not dist or len(dist) <= 1:
            return 0
        max_entropy = math.log2(len(dist))
        return entropy(dist) / max_entropy if max_entropy > 0 else 0

    country_score = normalized_entropy(countries) * 40
    decade_score = normalized_entropy(decades) * 30
    type_score = normalized_entropy(artist_types) * 30

    return round(country_score + decade_score + type_score, 1)


def get_available_filters(track_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Return available filter options (countries, decades, types) with track counts."""
    from django.db.models import Count, F

    countries = (
        Artist.objects
        .exclude(tracks__id__in=track_ids)
        .filter(origin_country__isnull=False)
        .exclude(origin_country='')
        .values('origin_country')
        .annotate(count=Count('tracks'))
        .filter(count__gte=3)
        .order_by('-count')[:20]
    )

    country_options = [
        {
            'code': c['origin_country'],
            'name': COUNTRY_NAMES.get(c['origin_country'], c['origin_country']),
            'count': c['count']
        }
        for c in countries
    ]

    decades = (
        Artist.objects
        .exclude(tracks__id__in=track_ids)
        .filter(formed_year__isnull=False)
        .annotate(decade=((F('formed_year') / 10) * 10))
        .values('decade')
        .annotate(count=Count('tracks'))
        .filter(count__gte=3)
        .order_by('-decade')
    )

    decade_options = [
        {
            'decade': d['decade'],
            'label': f"{d['decade']}s",
            'count': d['count']
        }
        for d in decades if d['decade'] and d['decade'] >= 1950
    ]

    types = (
        Artist.objects
        .exclude(tracks__id__in=track_ids)
        .filter(artist_type__isnull=False)
        .exclude(artist_type='')
        .values('artist_type')
        .annotate(count=Count('tracks'))
        .filter(count__gte=3)
        .order_by('-count')
    )

    type_options = [
        {'type': t['artist_type'], 'count': t['count']}
        for t in types
    ]

    region_options = []
    for region_key, region_countries in REGION_GROUPS.items():
        count = (
            Track.objects
            .exclude(id__in=track_ids)
            .filter(artist__origin_country__in=region_countries)
            .count()
        )
        if count >= 5:
            region_options.append({
                'key': region_key,
                'name': region_key.replace('_', ' ').title(),
                'count': count
            })

    region_options.sort(key=lambda x: -x['count'])

    return {
        'countries': country_options,
        'decades': decade_options,
        'artist_types': type_options,
        'regions': region_options
    }


def get_influence_recommendations(track_ids: list[str], limit: int = 10) -> dict[str, Any]:
    """Suggest tracks from older artists in the same genres as a proxy for musical influence."""
    playlist_artists = Artist.objects.filter(
        tracks__id__in=track_ids
    ).distinct()

    # TODO: use real influence data when Wikidata enrichment is wired up
    input_genres = set()
    for artist in playlist_artists:
        for track in artist.tracks.filter(id__in=track_ids):
            input_genres.update(track.genres.values_list('id', flat=True))

    if not input_genres:
        return {'recommendations': [], 'lineage': {}}

    older_artists = Artist.objects.filter(
        tracks__genres__id__in=list(input_genres),
        formed_year__isnull=False
    ).exclude(
        tracks__id__in=track_ids
    ).filter(
        formed_year__lt=min(
            a.formed_year for a in playlist_artists if a.formed_year
        ) if any(a.formed_year for a in playlist_artists) else 2000
    ).distinct().order_by('formed_year')[:20]

    influence_tracks = Track.objects.filter(
        artist__in=older_artists
    ).select_related('artist').order_by('-popularity')[:limit]

    lineage = {}
    for track in influence_tracks:
        lineage[track.id] = {
            'artist': track.artist.name,
            'era': f"{track.artist.decade}s" if track.artist.decade else "Unknown era",
            'reason': f"Pioneer from the {track.artist.decade}s in genres you enjoy"
        }

    return {
        'recommendations': list(influence_tracks),
        'lineage': lineage
    }


def calculate_diversity_from_external_data(recommendations: list[Track], artist_info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Calculate diversity stats from external data for the results page."""
    import math

    if not recommendations:
        return {}

    countries = {}
    decades = {}
    artist_types = {}
    genres = {}

    for track in recommendations:
        ext = artist_info.get(track.id, {})

        country_name = ext.get('country_name')
        if country_name:
            countries[country_name] = countries.get(country_name, 0) + 1

        decade = ext.get('decade')
        if decade:
            decades[decade] = decades.get(decade, 0) + 1

        artist_type = ext.get('type')
        if artist_type:
            artist_types[artist_type] = artist_types.get(artist_type, 0) + 1

        for genre in track.genres.all():
            genres[genre.name] = genres.get(genre.name, 0) + 1

    def sort_dist(d, limit=5):
        return dict(sorted(d.items(), key=lambda x: -x[1])[:limit])

    diversity_score = round(_compute_diversity_score(countries, decades, artist_types))

    return {
        'total_tracks': len(recommendations),
        'countries': sort_dist(countries),
        'country_count': len(countries),
        'decades': sort_dist(decades),
        'decade_count': len(decades),
        'artist_types': sort_dist(artist_types),
        'genres': sort_dist(genres, limit=8),
        'genre_count': len(genres),
        'diversity_score': diversity_score
    }


# --------------------------------------------------------------------------
# GENRE LINEAGE MAP
# --------------------------------------------------------------------------

def get_genre_lineage_data() -> dict[str, Any]:
    """Build a graph of genre co-occurrences across tracks.

    For every track that belongs to multiple genres, each pair of those
    genres forms an edge.  Edge weight = number of tracks sharing both
    genres (only edges with weight >= 2 are kept).

    Returns:
        dict with ``nodes`` (list of {id, name, count}) and
        ``edges`` (list of {source, target, weight}).
    """
    from collections import Counter
    from itertools import combinations

    # Fetch all tracks that have at least one genre, with their genres
    tracks_with_genres = (
        Track.objects
        .prefetch_related('genres')
        .filter(genres__isnull=False)
        .distinct()
    )

    genre_track_count: dict[str, int] = Counter()
    edge_counter: dict[tuple[str, str], int] = Counter()

    for track in tracks_with_genres:
        genre_names = sorted(g.name for g in track.genres.all())

        for name in genre_names:
            genre_track_count[name] += 1

        # Create edges between every pair of co-occurring genres
        if len(genre_names) >= 2:
            for g1, g2 in combinations(genre_names, 2):
                edge_counter[(g1, g2)] += 1

    # Build node list
    nodes = [
        {'id': name, 'name': name, 'count': count}
        for name, count in genre_track_count.items()
    ]

    # Build edge list, filtering to weight >= 2
    edges = [
        {'source': pair[0], 'target': pair[1], 'weight': weight}
        for pair, weight in edge_counter.items()
        if weight >= 2
    ]

    return {'nodes': nodes, 'edges': edges}


# External data now handled by LiveExternalDataService in external_data.py (cached in Redis).
