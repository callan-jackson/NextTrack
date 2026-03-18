"""Database models for the music recommendation system."""

import uuid
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Genre(models.Model):
    """Genre lookup table."""
    name = models.CharField(max_length=100, unique=True, db_index=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Artist(models.Model):
    """Artist info with optional MusicBrainz/Wikidata enrichment."""
    id = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=255, db_index=True)
    popularity = models.IntegerField(default=0)

    origin_country = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Country of origin"
    )
    musicbrainz_id = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        db_index=True,
        help_text="MusicBrainz MBID"
    )
    artist_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="E.g. Person, Group, Orchestra"
    )
    formed_year = models.IntegerField(
        blank=True,
        null=True,
        help_text="Year formed or born"
    )
    disbanded_year = models.IntegerField(
        blank=True,
        null=True,
        help_text="Year disbanded or died"
    )

    wikidata_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        db_index=True,
        help_text="Wikidata Q-identifier"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Short description from Wikidata"
    )

    enriched_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Last enrichment timestamp"
    )

    class Meta:
        ordering = ['-popularity', 'name']
        indexes = [
            models.Index(fields=['origin_country']),
            models.Index(fields=['formed_year']),
        ]

    def __str__(self):
        return self.name

    @property
    def is_enriched(self):
        """True if enrichment data has been fetched."""
        return self.musicbrainz_id is not None or self.wikidata_id is not None

    @property
    def decade(self):
        """Decade the artist formed, e.g. 1987 -> 1980."""
        if self.formed_year:
            return (self.formed_year // 10) * 10
        return None


class Track(models.Model):
    """Track with audio features used for recommendations."""
    id = models.CharField(max_length=100, primary_key=True)
    title = models.CharField(max_length=500, db_index=True)

    artist = models.ForeignKey(
        Artist,
        on_delete=models.CASCADE,
        related_name='tracks'
    )

    genres = models.ManyToManyField(Genre, related_name='tracks')

    # Audio features
    danceability = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    energy = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    loudness = models.FloatField(default=-10.0)
    valence = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    tempo = models.FloatField(
        default=120.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(300.0)]
    )
    acousticness = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    popularity = models.IntegerField(default=0)

    artist_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    artist_popularity = models.IntegerField(default=0)

    is_audio_analyzed = models.BooleanField(
        default=True,
        help_text="False if using default values"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        null=True,
        blank=True,
        help_text="When this track was added"
    )

    release_year = models.IntegerField(
        blank=True,
        null=True,
        help_text="Release year from Spotify album data"
    )

    class Meta:
        ordering = ['-popularity']
        indexes = [
            models.Index(fields=['valence', 'energy']),
            models.Index(fields=['popularity']),
            models.Index(fields=['is_audio_analyzed']),
            models.Index(fields=['created_at']),
            models.Index(fields=['release_year']),
        ]

    def __str__(self):
        return f"{self.title} - {self.artist.name}"

    @property
    def has_reliable_features(self):
        """True if audio features are from Spotify, not defaults."""
        return self.is_audio_analyzed

    @property
    def mood_tags(self):
        """Human-readable mood tags derived from audio features."""
        tags = []
        if self.energy > 0.7:
            tags.append('high_energy')
        if self.energy < 0.3 and self.acousticness > 0.6:
            tags.append('chill')
        if self.valence > 0.7:
            tags.append('happy')
        if self.valence < 0.3:
            tags.append('melancholy')
        if self.danceability > 0.7:
            tags.append('danceable')
        if self.acousticness > 0.7:
            tags.append('acoustic')
        if self.tempo > 140:
            tags.append('fast')
        if self.tempo < 90:
            tags.append('slow')
        return tags


class RecommendationFeedback(models.Model):
    """Stores user likes/dislikes on recommended tracks."""
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name='feedback'
    )
    score = models.BooleanField(help_text="True=like, False=dislike")

    session_key = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['score']),
        ]

    def __str__(self):
        sentiment = "Like" if self.score else "Dislike"
        return f"{sentiment}: {self.track.title}"


class UserSurvey(models.Model):
    """User experience survey responses."""
    SATISFACTION_CHOICES = [
        (1, 'Very Unsatisfied'),
        (2, 'Unsatisfied'),
        (3, 'Neutral'),
        (4, 'Satisfied'),
        (5, 'Very Satisfied'),
    ]

    DISCOVERY_CHOICES = [
        (1, 'None - all familiar'),
        (2, 'A few new tracks'),
        (3, 'About half were new'),
        (4, 'Most were new to me'),
        (5, 'All new discoveries'),
    ]

    ACCURACY_CHOICES = [
        (1, 'Not at all accurate'),
        (2, 'Slightly accurate'),
        (3, 'Moderately accurate'),
        (4, 'Very accurate'),
        (5, 'Extremely accurate'),
    ]

    overall_satisfaction = models.IntegerField(
        choices=SATISFACTION_CHOICES,
        help_text="Overall satisfaction rating"
    )
    discovery_rating = models.IntegerField(
        choices=DISCOVERY_CHOICES,
        help_text="How many new tracks discovered"
    )
    accuracy_rating = models.IntegerField(
        choices=ACCURACY_CHOICES,
        help_text="Recommendation accuracy rating"
    )

    liked_most = models.TextField(
        blank=True,
        null=True,
        help_text="What the user liked most"
    )
    improvement_suggestion = models.TextField(
        blank=True,
        null=True,
        help_text="Suggested improvements"
    )

    would_recommend = models.BooleanField(
        default=True,
        help_text="Would recommend to others"
    )

    session_key = models.TextField(blank=True, null=True)
    tracks_interacted = models.IntegerField(
        default=0,
        help_text="Tracks interacted with before survey"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User Survey'
        verbose_name_plural = 'User Surveys'

    def __str__(self):
        return f"Survey {self.id} - Satisfaction: {self.overall_satisfaction}/5"

    @property
    def average_score(self):
        """Average of all three ratings."""
        return (self.overall_satisfaction + self.discovery_rating + self.accuracy_rating) / 3


class AnalyticsEvent(models.Model):
    """Tracks user engagement events for analytics."""
    EVENT_TYPES = [
        ('search', 'Search Performed'),
        ('recommend', 'Recommendations Generated'),
        ('play', 'Track Played'),
        ('like', 'Track Liked'),
        ('dislike', 'Track Disliked'),
        ('add_playlist', 'Added to Playlist'),
        ('filter_applied', 'Filter Applied'),
        ('survey_completed', 'Survey Completed'),
    ]

    event_type = models.CharField(max_length=50, choices=EVENT_TYPES, db_index=True)
    session_key = models.TextField(blank=True, null=True, db_index=True)

    track_id = models.CharField(max_length=100, blank=True, null=True)
    metadata = models.JSONField(
        blank=True,
        null=True,
        help_text="Extra event data (search query, filter, etc.)"
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.event_type} at {self.created_at}"


class SharedPlaylist(models.Model):
    """Shareable playlist snapshot via unique link."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track_ids = models.JSONField(help_text="List of track IDs in the playlist")
    preferences = models.JSONField(blank=True, null=True, help_text="Optional preference settings")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True, help_text="Auto-expire after this date")
    view_count = models.IntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Shared playlist {self.id} ({len(self.track_ids)} tracks)"


class PrecomputedRecommendation(models.Model):
    """Materialized/precomputed recommendations for popular tracks.

    Stores precomputed nearest-neighbour results so that popular tracks
    can serve recommendations without recalculating on every request.
    """
    source_track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name='precomputed_recs',
    )
    recommended_track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name='precomputed_as_rec',
    )
    distance = models.FloatField(
        help_text="Euclidean distance between source and recommended track feature vectors"
    )
    computed_at = models.DateTimeField(
        auto_now=True,
        help_text="When this recommendation was last computed"
    )

    class Meta:
        unique_together = [('source_track', 'recommended_track')]
        ordering = ['distance']
        indexes = [
            models.Index(fields=['source_track']),
        ]

    def __str__(self):
        return (
            f"{self.source_track_id} -> {self.recommended_track_id} "
            f"(d={self.distance:.4f})"
        )


class Album(models.Model):
    """Album model for release context."""
    id = models.CharField(max_length=100, primary_key=True)
    title = models.CharField(max_length=500, db_index=True)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name='albums', null=True, blank=True)
    release_date = models.DateField(blank=True, null=True)
    album_type = models.CharField(max_length=50, blank=True, null=True, help_text="single, album, or compilation")
    spotify_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    cover_image_url = models.URLField(max_length=500, blank=True, null=True)
    total_tracks = models.IntegerField(default=0)

    class Meta:
        ordering = ['-release_date']

    def __str__(self):
        return self.title
