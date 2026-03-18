"""DRF serializers for the catalog API."""

from rest_framework import serializers

from catalog.models import Genre, Artist, Track


class GenreSerializer(serializers.ModelSerializer):
    """Serializes Genre id and name."""

    class Meta:
        model = Genre
        fields = ['id', 'name']


class ArtistSerializer(serializers.ModelSerializer):
    """Serializes basic artist info."""

    class Meta:
        model = Artist
        fields = ['id', 'name', 'popularity']


class TrackSerializer(serializers.ModelSerializer):
    """Full track detail with nested artist/genres and audio features."""
    artist = ArtistSerializer(read_only=True)
    genres = GenreSerializer(many=True, read_only=True)
    mood_tags = serializers.SerializerMethodField()

    class Meta:
        model = Track
        fields = [
            'id',
            'title',
            'artist',
            'genres',
            'danceability',
            'energy',
            'loudness',
            'valence',
            'tempo',
            'acousticness',
            'popularity',
            'mood_tags',
        ]

    def get_mood_tags(self, obj):
        return obj.mood_tags


class TrackListSerializer(serializers.ModelSerializer):
    """Lightweight track serializer for list views."""
    artist_name = serializers.CharField(source='artist.name', read_only=True)

    class Meta:
        model = Track
        fields = [
            'id',
            'title',
            'artist_name',
            'popularity',
            'valence',
            'energy',
        ]


class RecommendationSerializer(serializers.Serializer):
    """Wraps a source track and its recommendations."""
    source_track = TrackSerializer()
    recommendations = TrackSerializer(many=True)
    count = serializers.IntegerField()


class RecommendationRequestSerializer(serializers.Serializer):
    """Validates recommendation request input."""
    track_ids = serializers.ListField(
        child=serializers.CharField(max_length=100),
        min_length=1,
        max_length=50,
    )
    preferences = serializers.DictField(required=False, default=dict)
    limit = serializers.IntegerField(required=False, default=10, min_value=1, max_value=50)
