# Audio feature extraction: method and calibration

How `catalog/audio_analysis.py` turns a 30-second preview clip into the
five-dimensional vector the recommender ranks on, and how its constants were
chosen.

## Why this exists

The feature vector used to come from Spotify's `GET /audio-features`. That
endpoint was deprecated on 2024-11-27 and now returns `403` for any app without
a quota extension granted before that date. Tracks ingested afterwards were
stored with neutral `0.5` placeholders, which is worse than it sounds: the
recommender ranks by Euclidean distance from a playlist centroid, and a set of
identical vectors is equidistant from every centroid. Those tracks were not
badly ranked, they were unrankable.

Computing the vector from audio removes the dependency entirely. It also makes
the project's central claim literally true — recommendations are now derived
from the sound of the music rather than from numbers someone else measured.

## Backend choice: librosa, not Essentia

Essentia is the better DSP library for this task and it is what Spotify's own
values were largely derived from. It is not usable here: **Essentia publishes no
linux-aarch64 wheel** (verified across every release on PyPI), so it cannot be
installed in a container on Apple Silicon, which is the development target. The
options were emulating x86 under QEMU for every analysis job, or building
Essentia from source in the image. Neither is worth it.

librosa is pure Python over numpy/scipy/numba, and every package in the chain
has a cp311 aarch64 wheel. It is slower than Essentia's C++ but analysis is a
background job on a 30-second clip, so throughput is not the constraint.

`get_extractor()` is the seam: an Essentia backend can be added for x86-only
deployments without touching callers.

## What is measured and what is estimated

Not all five dimensions are equally well-founded, and the code says so.

| Dimension | Status | Basis |
|---|---|---|
| `tempo` | **Measured** | Onset-envelope beat tracking. Objective, modulo octave errors (a 76 BPM track may report 152). |
| `loudness` | **Measured** | RMS in dBFS. Objective. |
| `energy` | Estimated | Weighted: loudness, spectral rolloff, onset rate, percussive ratio. |
| `danceability` | Estimated | Weighted: percussive ratio, onset-envelope autocorrelation peak, tempo fit, onset rate — gated by rhythmic presence. |
| `acousticness` | Estimated | Weighted inverse of: percussive ratio, spectral flatness, high-frequency ratio, loudness. |
| `valence` | **Heuristic** | Mode (major/minor), brightness, tempo, energy. The weakest of the five — see below. |

### On valence

Spotify's valence came from a model trained on human mood ratings. Musical
positiveness is not recoverable from DSP alone, and nothing here claims
otherwise. The proxy computed from mode and brightness correlates loosely with
perceived mood and fails in the obvious way: Radiohead's *Creep* is
harmonically major (G–B–C–Cm) and reads ~0.69, despite being the reference
example of a miserable song. Treat valence as a weak signal.

## Two things that took a while to get right

**Beat-interval variance is not a measure of rhythm.** The first implementation
scored danceability partly on the consistency of intervals between detected
beats. Every track scored ~0.9, including a Chopin nocturne. The beat tracker is
an *estimator*: it fits an evenly spaced grid to whatever it is given, so the
intervals are regular by construction whether or not the audio has a pulse.
Replaced with the peak of the normalised autocorrelation of the onset envelope
over a 50–200 BPM lag range, which measures whether a periodicity actually
exists, plus the harmonic/percussive energy split.

**Ambient needs a gate, not a weight.** Even with better features, a track with
no onsets still collected free contributions from the tempo-fit term. Marconi
Union's *Weightless* scored 0.51 danceability. Danceability is now multiplied
by a rhythmic-presence gate derived from onset rate, so a track with no onsets
scores 0 regardless of what else fires.

## Calibration corpus

Normalisation bounds in `BOUNDS` are the observed near-min and near-max of each
raw descriptor across a 12-track corpus chosen to span the space: solo piano,
solo cello, ambient, singer-songwriter, alt rock, metal, disco-funk, pop-funk,
house and trance. Raw descriptor distributions were measured first, then bounds
were set from the data rather than guessed.

Output on that corpus, sorted by energy:

| track | energy | dance | acoustic | valence | tempo |
|---|---|---|---|---|---|
| Chopin – Nocturne | 0.10 | 0.25 | 1.00 | 0.14 | 99 |
| Bach – Cello Suite | 0.22 | 0.37 | 0.91 | 0.44 | 108 |
| Marconi Union – Weightless | 0.23 | 0.00 | 0.79 | 0.46 | 123 |
| Jeff Buckley – Hallelujah | 0.34 | 0.38 | 0.80 | 0.50 | 103 |
| The Beatles – Blackbird | 0.48 | 0.44 | 0.71 | 0.50 | 92 |
| Radiohead – Creep | 0.63 | 0.46 | 0.34 | 0.70 | 92 |
| Metallica – Master of Puppets | 0.72 | 0.66 | 0.17 | 0.67 | 103 |
| Daft Punk – Get Lucky | 0.82 | 0.78 | 0.38 | 0.53 | 117 |
| Michael Jackson – Billie Jean | 0.83 | 0.84 | 0.15 | 0.59 | 117 |
| Pharrell Williams – Happy | 0.85 | 0.69 | 0.17 | 0.65 | 162 |
| Daft Punk – One More Time | 0.86 | 0.92 | 0.05 | 0.77 | 123 |
| Darude – Sandstorm | 0.87 | 0.81 | 0.17 | 0.65 | 136 |

Acousticness separates cleanly across the whole range (1.00 → 0.05), energy is
monotonic against intuition, and danceability correctly puts ambient at zero
and house at the top. Verified identical on librosa 0.10.2 (container) and
1.0.0.

## Recalibrating

`ANALYSIS_VERSION` in `audio_analysis.py` is stamped onto every analysed track.
Bump it whenever the maths changes; the scheduled backfill then re-analyses
anything below the current version:

```bash
python manage.py analyze_audio --all --force
```

Each result also carries a `descriptors` dict of the raw measurements, so
bounds can be re-derived from stored data without re-downloading audio.

## Known limitations

- **30 seconds is not the whole track.** A song that changes character after
  the preview window is characterised by its opening only.
- **Octave errors in tempo** are inherent to beat tracking.
- **Previews are not universal.** Some catalogue entries have none; those keep
  neutral defaults and are flagged `is_audio_analyzed=False`.
- **Values are not comparable to Spotify's.** They are on the same 0–1 scale
  and ordered similarly, but they are a different measurement. Mixing
  CSV-imported Spotify-era values with locally analysed ones in the same
  distance computation is a real inconsistency; `analysis_version` marks which
  is which so a full re-analysis can settle it.
