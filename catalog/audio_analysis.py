"""Audio feature extraction from short preview clips.

Why this module exists
----------------------
NextTrack ranks tracks by a five-dimensional audio-feature vector (valence,
energy, danceability, acousticness, tempo). Those values used to come from
Spotify's ``/audio-features`` endpoint, which was deprecated on 2024-11-27 and
now returns 403 for any app without a pre-existing quota extension. Tracks
ingested after that date were stored with neutral 0.5 defaults, which makes
them equidistant from every centroid and therefore unrankable.

This module removes that dependency by computing the vector directly from
audio. Providers such as Deezer expose a 30-second preview MP3 for most of
their catalogue; that clip is enough to characterise a track's timbre, rhythm
and loudness.

Measured vs. estimated
----------------------
Being precise about this matters, because the five dimensions are not equally
well-founded:

* ``tempo`` and ``loudness`` are **measured**. Beat tracking and RMS loudness
  are standard DSP with an objective ground truth.
* ``energy``, ``danceability`` and ``acousticness`` are **estimated** from
  weighted combinations of measured descriptors (percussive/harmonic balance,
  onset density, spectral rolloff, spectral flatness, high-frequency ratio).
  The weights below were calibrated against a reference corpus spanning solo
  piano to hard techno - see ``docs/NOTES.audio-features.md``.
* ``valence`` is a **heuristic** and the weakest of the five. Spotify's
  original figure came from a model trained on human mood ratings; musical
  positiveness is not recoverable from DSP alone. What is computed here is a
  proxy built from mode (major/minor), brightness, tempo and energy. It
  correlates loosely with perceived mood and should be read as a weak signal.

The backend is pluggable. librosa is the default because it is the only option
that installs cleanly on linux/arm64 - Essentia publishes no aarch64 wheel, so
it cannot be used in a container on Apple Silicon. On x86_64 an Essentia
backend could be added behind ``get_extractor()`` without touching callers.
"""

import logging
import os
import tempfile
from typing import Any, Optional

import numpy as np
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Bumped whenever the extraction maths changes, so previously analysed tracks
# can be identified and re-analysed rather than silently mixing calibrations.
ANALYSIS_VERSION = 1

SAMPLE_RATE = 22050
EPS = 1e-10

_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 30
MAX_PREVIEW_BYTES = 10 * 1024 * 1024

# Krumhansl-Schmuckler key profiles, used to decide major vs minor.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# Normalisation bounds measured across the reference corpus. Each pair is the
# observed (near-min, near-max) for that descriptor; values outside clamp.
BOUNDS = {
    'loudness_db': (-27.0, -10.0),
    'rolloff_hz': (900.0, 7000.0),
    'onset_rate': (0.5, 5.5),
    'percussive': (0.13, 0.55),
    'autocorr_peak': (0.45, 0.85),
    'flatness': (0.001, 0.05),
    'hf_ratio': (0.01, 0.30),
    'centroid_hz': (700.0, 3400.0),
    'tempo_bpm': (60.0, 165.0),
    'mode_margin': (-0.30, 0.30),
    # A track with almost no onsets has no danceability regardless of what the
    # beat tracker reports, so rhythmic content gates the whole dimension.
    'rhythm_gate': (0.3, 2.5),
}

DANCE_TEMPO_CENTRE = 120.0
DANCE_TEMPO_SIGMA = 40.0


class AudioAnalysisError(Exception):
    """Raised when a preview cannot be fetched or decoded."""


def _clamp01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _norm(value: float, key: str) -> float:
    """Scale a raw descriptor onto 0-1 using its calibrated bounds."""
    lo, hi = BOUNDS[key]
    return _clamp01((value - lo) / (hi - lo))


def download_preview(url: str, timeout: Optional[tuple] = None) -> str:
    """Download a preview clip to a temp file and return its path.

    The caller owns the file and must delete it. We go via a real file rather
    than an in-memory buffer deliberately: libsndfile cannot sniff the MP3
    format through virtual IO once an ID3 tag is present, so decoding from
    BytesIO fails with "Format not recognised" on exactly the files providers
    actually serve.
    """
    timeout = timeout or (_CONNECT_TIMEOUT, _READ_TIMEOUT)

    try:
        response = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise AudioAnalysisError(f"Preview download failed: {exc}") from exc

    if response.status_code != 200:
        raise AudioAnalysisError(f"Preview download returned {response.status_code}")

    handle = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > MAX_PREVIEW_BYTES:
                raise AudioAnalysisError("Preview exceeded maximum size")
            handle.write(chunk)
        handle.close()
    except Exception:
        handle.close()
        os.unlink(handle.name)
        raise

    if total == 0:
        os.unlink(handle.name)
        raise AudioAnalysisError("Preview was empty")

    return handle.name


def _load_audio(path: str):
    """Decode an audio file to a mono waveform at SAMPLE_RATE."""
    import librosa

    try:
        y, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    except Exception as exc:
        raise AudioAnalysisError(f"Could not decode audio: {exc}") from exc

    if y.size == 0:
        raise AudioAnalysisError("Decoded audio was empty")

    y, _ = librosa.effects.trim(y, top_db=40)
    if y.size < SAMPLE_RATE:
        raise AudioAnalysisError("Decoded audio too short to analyse")

    # Digital silence survives trim() but makes every downstream descriptor
    # degenerate: the chroma correlation divides by a zero standard deviation
    # and the whole vector comes out NaN. Reject it here rather than letting
    # NaNs reach the database.
    if float(np.max(np.abs(y))) < 1e-6:
        raise AudioAnalysisError("Audio is silent")

    return y, sr


def _mode_margin(y, sr) -> tuple[float, str]:
    """Correlate the chroma profile against major and minor key templates.

    Returns the (major - minor) correlation margin and the winning mode.
    """
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)

    # A flat chroma profile (no pitched content) has zero variance, which makes
    # the correlations below NaN. Treat that as "no mode information".
    if float(profile.std()) < EPS:
        return 0.0, 'unknown'

    profile = (profile - profile.mean()) / (profile.std() + EPS)

    major = max(np.corrcoef(profile, np.roll(KS_MAJOR, k))[0, 1] for k in range(12))
    minor = max(np.corrcoef(profile, np.roll(KS_MINOR, k))[0, 1] for k in range(12))

    if not (np.isfinite(major) and np.isfinite(minor)):
        return 0.0, 'unknown'

    return float(major - minor), ('major' if major >= minor else 'minor')


def extract_features_from_file(path: str) -> dict[str, Any]:
    """Compute the audio-feature vector for a local audio file.

    Returns a dict with the five recommendation dimensions plus ``loudness``
    and a ``descriptors`` sub-dict holding the raw measurements, which is kept
    for debugging and for recalibration without re-downloading audio.
    """
    import librosa

    y, sr = _load_audio(path)
    duration = len(y) / sr

    # --- Loudness and dynamics -------------------------------------------
    rms = librosa.feature.rms(y=y)[0]
    loudness_db = float(20 * np.log10(np.sqrt(np.mean(rms ** 2)) + EPS))

    # --- Rhythm -----------------------------------------------------------
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo_raw, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    # librosa >=1.0 returns tempo as an array; 0.10 returns a scalar.
    tempo = float(np.atleast_1d(tempo_raw)[0])
    onset_count = len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr))
    onset_rate = onset_count / max(duration, 1.0)

    # Normalised autocorrelation of the onset envelope, peak-picked over a
    # musical lag range (50-200 BPM). This measures whether a real periodicity
    # exists, which beat-interval variance cannot: the beat tracker emits an
    # evenly spaced grid whether or not the audio has a pulse.
    autocorr = librosa.autocorrelate(onset_env, max_size=len(onset_env) // 2)
    autocorr = autocorr / (autocorr[0] + EPS)
    frames_per_sec = sr / 512
    lag_lo = int(frames_per_sec * 60 / 200)
    lag_hi = int(frames_per_sec * 60 / 50)
    autocorr_peak = float(autocorr[lag_lo:lag_hi].max()) if lag_hi < len(autocorr) else 0.0

    # --- Timbre -----------------------------------------------------------
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)))
    flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))

    harmonic, percussive = librosa.effects.hpss(y)
    h_rms = float(np.sqrt(np.mean(harmonic ** 2)))
    p_rms = float(np.sqrt(np.mean(percussive ** 2)))
    percussive_ratio = p_rms / (h_rms + p_rms + EPS)

    spectrum = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    hf_ratio = float(spectrum[freqs > 5000].sum() / (spectrum.sum() + EPS))

    # --- Derived dimensions ----------------------------------------------
    loud_n = _norm(loudness_db, 'loudness_db')
    rolloff_n = _norm(rolloff, 'rolloff_hz')
    onset_n = _norm(onset_rate, 'onset_rate')
    perc_n = _norm(percussive_ratio, 'percussive')
    autocorr_n = _norm(autocorr_peak, 'autocorr_peak')

    # Energy: perceived intensity and activity.
    energy = _clamp01(
        0.35 * loud_n
        + 0.25 * rolloff_n
        + 0.20 * onset_n
        + 0.20 * perc_n
    )

    # Danceability: rhythmic regularity and percussive drive, gated by whether
    # the track has any rhythmic content at all.
    tempo_fit = float(np.exp(-((tempo - DANCE_TEMPO_CENTRE) ** 2) / (2 * DANCE_TEMPO_SIGMA ** 2)))
    rhythm_gate = _norm(onset_rate, 'rhythm_gate')
    danceability = _clamp01(
        rhythm_gate * (
            0.35 * perc_n
            + 0.30 * autocorr_n
            + 0.20 * tempo_fit
            + 0.15 * onset_n
        )
    )

    # Acousticness: absence of the markers of electronic/amplified production
    # (percussive transients, noisy spectra, high-frequency content, loud
    # compressed mastering).
    acousticness = _clamp01(
        0.30 * (1 - perc_n)
        + 0.25 * (1 - _norm(flatness, 'flatness'))
        + 0.25 * (1 - _norm(hf_ratio, 'hf_ratio'))
        + 0.20 * (1 - loud_n)
    )

    # Valence: heuristic proxy. See module docstring - treat as a weak signal.
    margin, mode = _mode_margin(y, sr)
    valence = _clamp01(
        0.40 * _norm(margin, 'mode_margin')
        + 0.20 * _norm(centroid, 'centroid_hz')
        + 0.15 * _norm(tempo, 'tempo_bpm')
        + 0.15 * energy
        + 0.10 * perc_n
    )

    # Final guard: the model's validators reject out-of-range values and a NaN
    # would poison every distance the recommender computes, so refuse to return
    # a vector that is not finite rather than persisting one.
    computed = {
        'valence': valence,
        'energy': energy,
        'danceability': danceability,
        'acousticness': acousticness,
        'tempo': tempo,
        'loudness': loudness_db,
    }
    bad = [name for name, value in computed.items() if not np.isfinite(value)]
    if bad:
        raise AudioAnalysisError(f"Non-finite feature values: {', '.join(sorted(bad))}")

    return {
        'valence': round(valence, 4),
        'energy': round(energy, 4),
        'danceability': round(danceability, 4),
        'acousticness': round(acousticness, 4),
        'tempo': round(max(0.0, min(300.0, tempo)), 2),
        'loudness': round(max(-60.0, min(0.0, loudness_db)), 2),
        'analysis_version': ANALYSIS_VERSION,
        'descriptors': {
            'duration_s': round(duration, 2),
            'loudness_db': round(loudness_db, 2),
            'onset_rate': round(onset_rate, 3),
            'autocorr_peak': round(autocorr_peak, 3),
            'percussive_ratio': round(percussive_ratio, 3),
            'spectral_centroid': round(centroid, 1),
            'spectral_rolloff': round(rolloff, 1),
            'spectral_flatness': round(flatness, 5),
            'hf_ratio': round(hf_ratio, 4),
            'mode': mode,
            'mode_margin': round(margin, 3),
        },
    }


def analyze_preview_url(url: str) -> dict[str, Any]:
    """Download a preview clip, extract its features, and clean up."""
    path = download_preview(url)
    try:
        return extract_features_from_file(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            logger.warning(f"Could not remove temp preview {path}")


def is_available() -> bool:
    """True if the analysis backend is installed.

    librosa is an optional dependency: the app degrades to neutral defaults
    without it rather than failing to boot, which keeps the web container
    runnable even if only the worker image carries the audio stack.
    """
    if not getattr(settings, 'AUDIO_ANALYSIS_ENABLED', True):
        return False
    try:
        import librosa  # noqa: F401
    except ImportError:
        return False
    return True
