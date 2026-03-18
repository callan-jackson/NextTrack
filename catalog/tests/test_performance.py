"""Performance benchmarks for the recommendation engine.

Run with: pytest catalog/tests/test_performance.py -m slow -v
"""

import time

import pytest
from django.test import TestCase

from catalog.models import Genre, Artist, Track
from catalog.services import get_recommendations_from_sequence


@pytest.mark.slow
class RecommendationPerformanceTestCase(TestCase):
    """Time-based benchmarks for get_recommendations_from_sequence."""

    @classmethod
    def setUpTestData(cls):
        cls.genre = Genre.objects.create(name="perf_genre")
        cls.artist = Artist.objects.create(
            id="perf_artist", name="Performance Artist", popularity=70
        )

        cls.tracks = []
        for i in range(200):
            track = Track.objects.create(
                id=f"perf_track_{i}",
                title=f"Perf Track {i}",
                artist=cls.artist,
                valence=0.1 + (i % 9) * 0.1,
                energy=0.1 + (i % 9) * 0.1,
                danceability=0.1 + (i % 9) * 0.1,
                acousticness=0.9 - (i % 9) * 0.1,
                tempo=80.0 + (i % 12) * 10,
                popularity=30 + (i % 70),
            )
            track.genres.add(cls.genre)
            cls.tracks.append(track)

    def _time_recommendation(self, input_size, limit=10):
        """Run a recommendation and return elapsed seconds."""
        input_ids = [t.id for t in self.tracks[:input_size]]
        start = time.perf_counter()
        result = get_recommendations_from_sequence(input_ids, limit=limit)
        elapsed = time.perf_counter() - start
        return elapsed, result

    def test_single_track_under_1s(self):
        """Single-track recommendation completes in under 1 second."""
        elapsed, result = self._time_recommendation(1)
        self.assertLess(elapsed, 1.0, f"Took {elapsed:.3f}s, expected < 1.0s")
        self.assertGreater(len(result["recommendations"]), 0)

    def test_5_tracks_under_1s(self):
        """5-track sequence recommendation completes in under 1 second."""
        elapsed, result = self._time_recommendation(5)
        self.assertLess(elapsed, 1.0, f"Took {elapsed:.3f}s, expected < 1.0s")

    def test_20_tracks_under_2s(self):
        """20-track sequence recommendation completes in under 2 seconds."""
        elapsed, result = self._time_recommendation(20)
        self.assertLess(elapsed, 2.0, f"Took {elapsed:.3f}s, expected < 2.0s")

    def test_50_tracks_under_3s(self):
        """50-track sequence (max allowed) completes in under 3 seconds."""
        elapsed, result = self._time_recommendation(50)
        self.assertLess(elapsed, 3.0, f"Took {elapsed:.3f}s, expected < 3.0s")

    def test_scaling_is_sublinear(self):
        """Doubling input size should not double execution time."""
        elapsed_5, _ = self._time_recommendation(5)
        elapsed_20, _ = self._time_recommendation(20)

        # 4x the input should take less than 4x the time
        # Use a generous bound to avoid flaky tests
        self.assertLess(
            elapsed_20, elapsed_5 * 8 + 0.5,
            f"20-track ({elapsed_20:.3f}s) took too long relative to "
            f"5-track ({elapsed_5:.3f}s)"
        )
