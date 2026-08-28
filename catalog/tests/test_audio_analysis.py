"""Tests for local audio feature extraction.

Signals are synthesised in-process, so these tests need no network and no
fixture audio files. They assert *relative* behaviour - a pure tone must read
as more acoustic than white noise - rather than exact values, which would make
the suite brittle against recalibration.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import TestCase, override_settings

from catalog.audio_analysis import (
    ANALYSIS_VERSION,
    AudioAnalysisError,
    _clamp01,
    _norm,
    analyze_preview_url,
    extract_features_from_file,
    is_available,
)
from catalog.models import Artist, Track

try:
    import soundfile as sf
    AUDIO_STACK = is_available()
except ImportError:
    AUDIO_STACK = False

SR = 22050
DURATION = 8.0


def write_wav(samples, sr=SR):
    """Write a float waveform to a temp .wav and return its path."""
    handle = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    handle.close()
    sf.write(handle.name, samples.astype(np.float32), sr)
    return handle.name


def sine(freq=440.0, duration=DURATION, sr=SR, amplitude=0.3):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return amplitude * np.sin(2 * np.pi * freq * t)


def white_noise(duration=DURATION, sr=SR, amplitude=0.3, seed=0):
    return np.random.default_rng(seed).normal(0, amplitude, int(sr * duration))


def click_train(bpm=120.0, duration=DURATION, sr=SR, seed=1):
    """Percussive hits on a steady grid over a quiet noise floor."""
    signal = np.random.default_rng(seed).normal(0, 0.01, int(sr * duration))
    interval = int(sr * 60.0 / bpm)
    decay = np.exp(-np.linspace(0, 12, int(sr * 0.05)))
    burst = np.random.default_rng(seed + 1).normal(0, 1, decay.size) * decay
    for start in range(0, signal.size - burst.size, interval):
        signal[start:start + burst.size] += burst * 0.8
    return signal


class NormalisationTestCase(TestCase):
    """The scaling helpers underpinning every derived dimension."""

    def test_clamp_bounds(self):
        self.assertEqual(_clamp01(-3.0), 0.0)
        self.assertEqual(_clamp01(0.42), 0.42)
        self.assertEqual(_clamp01(9.0), 1.0)

    def test_norm_maps_bounds_to_unit_interval(self):
        self.assertEqual(_norm(-27.0, 'loudness_db'), 0.0)
        self.assertEqual(_norm(-10.0, 'loudness_db'), 1.0)
        self.assertAlmostEqual(_norm(-18.5, 'loudness_db'), 0.5, places=2)

    def test_norm_clamps_outside_calibrated_range(self):
        self.assertEqual(_norm(-100.0, 'loudness_db'), 0.0)
        self.assertEqual(_norm(50.0, 'loudness_db'), 1.0)


@unittest.skipUnless(AUDIO_STACK, "audio analysis backend not installed")
class FeatureExtractionTestCase(TestCase):
    """End-to-end extraction over synthetic signals."""

    def setUp(self):
        self._paths = []

    def tearDown(self):
        for path in self._paths:
            if os.path.exists(path):
                os.unlink(path)

    def extract(self, samples):
        path = write_wav(samples)
        self._paths.append(path)
        return extract_features_from_file(path)

    def test_returns_every_expected_dimension(self):
        features = self.extract(click_train())

        for key in ('valence', 'energy', 'danceability', 'acousticness', 'tempo', 'loudness'):
            self.assertIn(key, features)
        self.assertEqual(features['analysis_version'], ANALYSIS_VERSION)

    def test_values_satisfy_model_validators(self):
        """Outputs must be storable in Track without tripping its validators."""
        features = self.extract(click_train())

        for key in ('valence', 'energy', 'danceability', 'acousticness'):
            self.assertGreaterEqual(features[key], 0.0, key)
            self.assertLessEqual(features[key], 1.0, key)

        self.assertGreaterEqual(features['tempo'], 0.0)
        self.assertLessEqual(features['tempo'], 300.0)
        self.assertGreaterEqual(features['loudness'], -60.0)
        self.assertLessEqual(features['loudness'], 0.0)

    def test_pure_tone_reads_more_acoustic_than_white_noise(self):
        """Spectral flatness and the harmonic/percussive split should separate these."""
        tone = self.extract(sine())
        noise = self.extract(white_noise())

        self.assertGreater(tone['acousticness'], noise['acousticness'])

    def test_sustained_tone_has_no_danceability(self):
        """No onsets means no rhythm, whatever tempo the beat tracker reports."""
        self.assertLess(self.extract(sine())['danceability'], 0.15)

    def test_click_train_is_more_danceable_than_a_drone(self):
        self.assertGreater(self.extract(click_train())['danceability'],
                           self.extract(sine())['danceability'])

    def test_louder_signal_reads_as_higher_energy(self):
        quiet = self.extract(click_train() * 0.05)
        loud = self.extract(click_train())

        self.assertGreater(loud['energy'], quiet['energy'])
        self.assertGreater(loud['loudness'], quiet['loudness'])

    def test_tempo_recovered_from_a_steady_pulse(self):
        """Allow octave errors, which are inherent to beat tracking."""
        tempo = self.extract(click_train(bpm=120))['tempo']
        self.assertTrue(
            any(abs(tempo - t) < 8 for t in (60, 120, 240)),
            f"tempo {tempo} not near 120 or an octave of it",
        )

    def test_descriptors_exposed_for_recalibration(self):
        descriptors = self.extract(click_train())['descriptors']

        for key in ('onset_rate', 'percussive_ratio', 'spectral_flatness', 'mode'):
            self.assertIn(key, descriptors)

    def test_silent_audio_rejected(self):
        with self.assertRaises(AudioAnalysisError):
            self.extract(np.zeros(int(SR * DURATION)))

    def test_too_short_audio_rejected(self):
        with self.assertRaises(AudioAnalysisError):
            self.extract(sine(duration=0.2))

    def test_unreadable_file_rejected(self):
        handle = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        handle.write(b'this is not audio')
        handle.close()
        self._paths.append(handle.name)

        with self.assertRaises(AudioAnalysisError):
            extract_features_from_file(handle.name)


class PreviewDownloadTestCase(TestCase):
    """Network failure modes surface as AudioAnalysisError, never raw requests errors."""

    def test_non_200_raises(self):
        response = MagicMock(status_code=404)
        with patch('catalog.audio_analysis.requests.get', return_value=response):
            with self.assertRaises(AudioAnalysisError):
                analyze_preview_url('https://example.invalid/x.mp3')

    def test_connection_error_raises(self):
        import requests

        with patch('catalog.audio_analysis.requests.get',
                   side_effect=requests.ConnectionError('no route')):
            with self.assertRaises(AudioAnalysisError):
                analyze_preview_url('https://example.invalid/x.mp3')

    def test_empty_body_raises(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = iter([])
        with patch('catalog.audio_analysis.requests.get', return_value=response):
            with self.assertRaises(AudioAnalysisError):
                analyze_preview_url('https://example.invalid/x.mp3')

    def test_oversized_body_rejected(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = iter([b'x' * (1024 * 1024)] * 20)
        with patch('catalog.audio_analysis.requests.get', return_value=response):
            with self.assertRaises(AudioAnalysisError):
                analyze_preview_url('https://example.invalid/big.mp3')


class AvailabilityTestCase(TestCase):
    """The backend can be switched off without breaking anything."""

    @override_settings(AUDIO_ANALYSIS_ENABLED=False)
    def test_disabled_by_setting(self):
        self.assertFalse(is_available())


class AnalyzeTaskTestCase(TestCase):
    """The Celery task that persists extracted features."""

    def setUp(self):
        self.artist = Artist.objects.create(id='radiohead', name='Radiohead')
        self.track = Track.objects.create(
            id='dz-1', title='Creep', artist=self.artist,
            is_audio_analyzed=False, analysis_version=0,
            preview_url='https://example.invalid/preview.mp3',
        )
        self.features = {
            'valence': 0.21, 'energy': 0.63, 'danceability': 0.46,
            'acousticness': 0.34, 'tempo': 92.0, 'loudness': -15.0,
            'analysis_version': ANALYSIS_VERSION, 'descriptors': {},
        }

    def run_task(self, **kwargs):
        from catalog.tasks import analyze_track_audio_task

        return analyze_track_audio_task(self.track.id, **kwargs)

    def test_features_persisted(self):
        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.audio_analysis.analyze_preview_url', return_value=self.features), \
             patch('catalog.tasks._refresh_preview_url', return_value=self.track.preview_url):
            result = self.run_task()

        self.assertEqual(result['status'], 'success')
        self.track.refresh_from_db()
        self.assertTrue(self.track.is_audio_analyzed)
        self.assertEqual(self.track.analysis_version, ANALYSIS_VERSION)
        self.assertAlmostEqual(self.track.energy, 0.63)
        self.assertAlmostEqual(self.track.tempo, 92.0)
        self.assertIsNotNone(self.track.analyzed_at)

    def test_already_analysed_track_skipped(self):
        self.track.is_audio_analyzed = True
        self.track.analysis_version = ANALYSIS_VERSION
        self.track.save()

        with patch('catalog.audio_analysis.is_available', return_value=True):
            result = self.run_task()

        self.assertEqual(result['status'], 'skipped')

    def test_force_reanalyses(self):
        self.track.is_audio_analyzed = True
        self.track.analysis_version = ANALYSIS_VERSION
        self.track.save()

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.audio_analysis.analyze_preview_url', return_value=self.features), \
             patch('catalog.tasks._refresh_preview_url', return_value=self.track.preview_url):
            result = self.run_task(force=True)

        self.assertEqual(result['status'], 'success')

    def test_stale_preview_url_is_refetched_once(self):
        """Deezer signs preview URLs with an expiry, so one retry is expected."""
        fresh = 'https://example.invalid/fresh.mp3'
        attempts = []

        def analyse(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise AudioAnalysisError('403')
            return self.features

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.audio_analysis.analyze_preview_url', side_effect=analyse), \
             patch('catalog.tasks._refresh_preview_url', return_value=fresh):
            result = self.run_task()

        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(attempts), 2)
        self.track.refresh_from_db()
        self.assertEqual(self.track.preview_url, fresh)

    def test_track_without_preview_is_skipped_not_failed(self):
        self.track.preview_url = None
        self.track.save()

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.tasks._refresh_preview_url', return_value=None):
            result = self.run_task()

        self.assertEqual(result['status'], 'skipped')
        self.track.refresh_from_db()
        self.assertFalse(self.track.is_audio_analyzed)

    def test_missing_track_reported(self):
        from catalog.tasks import analyze_track_audio_task

        with patch('catalog.audio_analysis.is_available', return_value=True):
            result = analyze_track_audio_task('does-not-exist')

        self.assertEqual(result['status'], 'error')

    def test_backend_absent_skips_cleanly(self):
        with patch('catalog.audio_analysis.is_available', return_value=False):
            self.assertEqual(self.run_task()['status'], 'skipped')


class BackfillTaskTestCase(TestCase):
    """The sweep must only queue tracks that can actually be analysed."""

    def setUp(self):
        self.artist = Artist.objects.create(id='a', name='A')

    def test_legacy_tracks_without_previews_are_not_queued(self):
        """CSV rows have real features and no preview - queueing them is pure churn."""
        Track.objects.create(id='csv-1', title='Legacy', artist=self.artist,
                             is_audio_analyzed=True, analysis_version=0, preview_url=None)

        from catalog.tasks import backfill_audio_analysis_task

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.tasks.analyze_track_audio_task.apply_async') as delay:
            result = backfill_audio_analysis_task()

        self.assertEqual(result['queued'], 0)
        delay.assert_not_called()

    def test_deezer_tracks_are_queued(self):
        Track.objects.create(id='dz-2', title='New', artist=self.artist,
                             is_audio_analyzed=False, analysis_version=0)

        from catalog.tasks import backfill_audio_analysis_task

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.tasks.analyze_track_audio_task.apply_async') as delay:
            result = backfill_audio_analysis_task()

        self.assertEqual(result['queued'], 1)
        delay.assert_called_once_with(args=['dz-2'], priority=9)

    def test_tracks_with_preview_urls_are_queued(self):
        Track.objects.create(id='other-1', title='X', artist=self.artist,
                             is_audio_analyzed=False, analysis_version=0,
                             preview_url='https://example.invalid/p.mp3')

        from catalog.tasks import backfill_audio_analysis_task

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.tasks.analyze_track_audio_task.apply_async') as delay:
            result = backfill_audio_analysis_task()

        self.assertEqual(result['queued'], 1)
        delay.assert_called_once_with(args=['other-1'], priority=9)

    def test_respects_limit(self):
        for index in range(5):
            Track.objects.create(id=f'dz-{index}', title=f'T{index}', artist=self.artist,
                                 is_audio_analyzed=False, analysis_version=0)

        from catalog.tasks import backfill_audio_analysis_task

        with patch('catalog.audio_analysis.is_available', return_value=True), \
             patch('catalog.tasks.analyze_track_audio_task.apply_async'):
            result = backfill_audio_analysis_task(limit=2)

        self.assertEqual(result['queued'], 2)
