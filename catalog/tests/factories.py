"""Factory classes for catalog test data using factory_boy."""

import factory
from catalog.models import Genre, Artist, Track, RecommendationFeedback


class GenreFactory(factory.django.DjangoModelFactory):
    """Factory for Genre model."""

    class Meta:
        model = Genre
        django_get_or_create = ('name',)

    name = factory.Sequence(lambda n: f'genre_{n}')


class ArtistFactory(factory.django.DjangoModelFactory):
    """Factory for Artist model."""

    class Meta:
        model = Artist

    id = factory.Sequence(lambda n: f'artist_{n}')
    name = factory.Sequence(lambda n: f'Artist {n}')
    popularity = 50
    origin_country = None
    artist_type = None
    formed_year = None


class TrackFactory(factory.django.DjangoModelFactory):
    """Factory for Track model."""

    class Meta:
        model = Track

    id = factory.Sequence(lambda n: f'track_{n}')
    title = factory.Sequence(lambda n: f'Track {n}')
    artist = factory.SubFactory(ArtistFactory)
    valence = 0.5
    energy = 0.5
    danceability = 0.5
    acousticness = 0.5
    tempo = 120.0
    popularity = 50
    is_audio_analyzed = True

    @factory.post_generation
    def genres(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for genre in extracted:
                self.genres.add(genre)


class RecommendationFeedbackFactory(factory.django.DjangoModelFactory):
    """Factory for RecommendationFeedback model."""

    class Meta:
        model = RecommendationFeedback

    track = factory.SubFactory(TrackFactory)
    score = True
    session_key = factory.Sequence(lambda n: f'session_{n}')
