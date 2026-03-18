"""
Proof of Adaptation Simulation Script

This management command demonstrates that the Adaptive Weighted Centroid algorithm
actually works by mathematically proving that user feedback causes the recommendation
centroid to "drift" in the feature space.

Algorithm Proof:
1. Baseline: Generate recommendations with no feedback -> Centroid A
2. Feedback: Simulate a "Like" on the first recommended track
3. Adaptive: Generate recommendations again with feedback -> Centroid B
4. Math: Calculate Euclidean distance between Centroid A and Centroid B

Expected Result:
The distance should be > 0, proving that feedback mathematically influences
the recommendation vector space.

Usage:
    python manage.py simulate_evaluation
    python manage.py simulate_evaluation --track-count 5
    python manage.py simulate_evaluation --verbose
"""

import random
import numpy as np
from django.core.management.base import BaseCommand

from catalog.models import Track, RecommendationFeedback
from catalog.services import (
    get_recommendations_from_sequence,
    centroid_to_vector,
    euclidean_distance,
    LIKE_WEIGHT,
    DISLIKE_WEIGHT
)


class Command(BaseCommand):
    help = 'Simulate and prove that user feedback adapts the recommendation centroid'

    def add_arguments(self, parser):
        parser.add_argument(
            '--track-count',
            type=int,
            default=3,
            help='Number of random tracks to use as input (default: 3)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed centroid values'
        )
        parser.add_argument(
            '--session-key',
            type=str,
            default='simulation_test_session',
            help='Session key for feedback simulation'
        )

    def handle(self, *args, **options):
        track_count = options['track_count']
        verbose = options['verbose']
        session_key = options['session_key']

        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('ADAPTIVE WEIGHTED CENTROID - PROOF OF ADAPTATION'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write('')

        # Step 1: Select random input tracks
        self.stdout.write(self.style.MIGRATE_HEADING('Step 1: Selecting Random Input Tracks'))

        all_track_ids = list(Track.objects.values_list('id', flat=True)[:1000])
        if len(all_track_ids) < track_count:
            self.stdout.write(self.style.ERROR(
                f'Not enough tracks in database. Need {track_count}, found {len(all_track_ids)}'
            ))
            return

        input_track_ids = random.sample(all_track_ids, track_count)
        input_tracks = Track.objects.filter(id__in=input_track_ids).select_related('artist')

        self.stdout.write(f'Selected {track_count} random tracks:')
        for track in input_tracks:
            self.stdout.write(f'  - {track.title} by {track.artist.name}')
        self.stdout.write('')

        # Step 2: Baseline - Generate recommendations WITHOUT feedback
        self.stdout.write(self.style.MIGRATE_HEADING('Step 2: Baseline Recommendations (No Feedback)'))

        # Clear any existing feedback for this session
        RecommendationFeedback.objects.filter(session_key=session_key).delete()

        baseline_result = get_recommendations_from_sequence(
            track_ids=input_track_ids,
            limit=10,
            session_key=session_key
        )

        centroid_a = baseline_result['centroid']
        centroid_a_vector = centroid_to_vector(centroid_a)

        self.stdout.write(f'Baseline Centroid A:')
        if verbose:
            for key, value in centroid_a.items():
                self.stdout.write(f'  {key}: {value:.4f}')
        else:
            self.stdout.write(f'  Vector: [{", ".join(f"{v:.3f}" for v in centroid_a_vector)}]')

        self.stdout.write(f'Feedback Applied: {baseline_result["feedback_applied"]}')
        self.stdout.write(f'Recommendations: {len(baseline_result["recommendations"])} tracks')
        self.stdout.write('')

        # Step 3: Simulate Feedback - "Like" the first recommended track
        self.stdout.write(self.style.MIGRATE_HEADING('Step 3: Simulating User Feedback'))

        if not baseline_result['recommendations']:
            self.stdout.write(self.style.ERROR('No recommendations to provide feedback on!'))
            return

        liked_track = baseline_result['recommendations'][0]
        self.stdout.write(f'Simulating "LIKE" on: {liked_track.title} by {liked_track.artist.name}')

        # Create feedback entry
        feedback = RecommendationFeedback.objects.create(
            track=liked_track,
            score=True,  # Like
            session_key=session_key
        )
        self.stdout.write(f'Created feedback entry: ID={feedback.id}, Score=Like')
        self.stdout.write('')

        # Step 4: Adaptive - Generate recommendations WITH feedback
        self.stdout.write(self.style.MIGRATE_HEADING('Step 4: Adaptive Recommendations (With Feedback)'))

        adaptive_result = get_recommendations_from_sequence(
            track_ids=input_track_ids,
            limit=10,
            session_key=session_key
        )

        centroid_b = adaptive_result['centroid']
        centroid_b_vector = centroid_to_vector(centroid_b)

        self.stdout.write(f'Adaptive Centroid B:')
        if verbose:
            for key, value in centroid_b.items():
                self.stdout.write(f'  {key}: {value:.4f}')
        else:
            self.stdout.write(f'  Vector: [{", ".join(f"{v:.3f}" for v in centroid_b_vector)}]')

        self.stdout.write(f'Feedback Applied: {adaptive_result["feedback_applied"]}')
        self.stdout.write(f'Recommendations: {len(adaptive_result["recommendations"])} tracks')
        self.stdout.write('')

        # Step 5: Mathematical Proof - Calculate distance between centroids
        self.stdout.write(self.style.MIGRATE_HEADING('Step 5: Mathematical Proof of Adaptation'))

        distance = euclidean_distance(centroid_a_vector, centroid_b_vector)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(
            f'User feedback moved the recommendation centroid by {distance:.6f} units'
        ))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('')

        # Detailed analysis
        if verbose:
            self.stdout.write(self.style.MIGRATE_HEADING('Detailed Analysis'))

            # Calculate per-dimension changes
            dimension_changes = centroid_b_vector - centroid_a_vector
            dimension_names = ['valence', 'energy', 'danceability', 'acousticness', 'tempo_norm']

            self.stdout.write('Per-dimension centroid drift:')
            for name, change in zip(dimension_names, dimension_changes):
                direction = '↑' if change > 0 else '↓' if change < 0 else '='
                self.stdout.write(f'  {name}: {change:+.6f} {direction}')

            self.stdout.write('')
            self.stdout.write(f'Algorithm Parameters:')
            self.stdout.write(f'  LIKE_WEIGHT: {LIKE_WEIGHT}')
            self.stdout.write(f'  DISLIKE_WEIGHT: {DISLIKE_WEIGHT}')

        # Interpretation
        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('INTERPRETATION:'))
        if distance > 0:
            self.stdout.write(self.style.SUCCESS(
                '✓ The adaptive algorithm IS working. User feedback caused '
                'measurable drift in the recommendation space.'
            ))
            self.stdout.write(self.style.SUCCESS(
                '✓ This proves the system "learns" from user interactions.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '⚠ No centroid drift detected. This may indicate the feedback '
                'was not applied correctly.'
            ))

        # Cleanup
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Cleanup'))
        deleted_count, _ = RecommendationFeedback.objects.filter(session_key=session_key).delete()
        self.stdout.write(f'Cleaned up {deleted_count} test feedback entries')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Simulation complete!'))
