/**
 * NextTrack preview player
 * ---------------------------------------------------------------------------
 * Plays the 30-second clip that NextTrack already analyses to derive each
 * track's audio features, so what you hear is exactly what the recommender
 * measured.
 *
 * This replaces the Spotify embed iframes used previously. Those keyed off
 * {{ track.id }} being a Spotify ID, which stopped being true when search
 * moved to Deezer, so the players silently broke for every newly ingested
 * track.
 *
 * Two constraints shape the design:
 *
 *   1. Deezer signs preview URLs with a ~24h expiry, so the URL stored at
 *      ingest time is usually dead. The clip URL is fetched on demand from
 *      /ajax/track-preview/ instead of being embedded in the page.
 *   2. Only one clip should ever play at a time, so a single shared Audio
 *      element is reused rather than one per card.
 *
 * Markup contract - any element like this becomes a player:
 *   <button class="preview-btn" data-track-id="dz-123" data-track-title="..."></button>
 */
(function () {
    'use strict';

    var audio = null;
    var currentButton = null;
    var urlCache = {};

    function getAudio() {
        if (audio) return audio;
        audio = new Audio();
        audio.preload = 'none';

        audio.addEventListener('timeupdate', function () {
            if (!currentButton || !audio.duration) return;
            var pct = (audio.currentTime / audio.duration) * 100;
            var bar = currentButton.querySelector('.preview-progress-fill');
            if (bar) bar.style.width = pct + '%';
        });

        audio.addEventListener('ended', function () { reset(); });
        audio.addEventListener('error', function () {
            if (currentButton) setState(currentButton, 'error');
            reset(true);
        });

        return audio;
    }

    function setState(button, state) {
        button.dataset.state = state;
        var icon = button.querySelector('i');
        if (!icon) return;

        var classes = {
            playing: 'fa-solid fa-pause',
            loading: 'fa-solid fa-spinner fa-spin',
            error: 'fa-solid fa-triangle-exclamation',
            idle: 'fa-solid fa-play'
        };
        icon.className = classes[state] || classes.idle;

        var title = button.dataset.trackTitle || 'track';
        if (state === 'playing') {
            button.setAttribute('aria-label', 'Pause preview of ' + title);
        } else if (state === 'error') {
            button.setAttribute('aria-label', 'Preview unavailable for ' + title);
            button.title = 'Preview unavailable';
        } else {
            button.setAttribute('aria-label', 'Play 30 second preview of ' + title);
        }
    }

    function reset(keepError) {
        if (currentButton) {
            if (!keepError) setState(currentButton, 'idle');
            var bar = currentButton.querySelector('.preview-progress-fill');
            if (bar) bar.style.width = '0%';
            currentButton.classList.remove('is-playing');
        }
        currentButton = null;
    }

    function stopCurrent() {
        if (audio && !audio.paused) audio.pause();
        reset();
    }

    function fetchPreviewUrl(trackId) {
        if (urlCache[trackId]) return Promise.resolve(urlCache[trackId]);

        return fetch('/ajax/track-preview/?id=' + encodeURIComponent(trackId))
            .then(function (r) {
                if (!r.ok) throw new Error('no preview');
                return r.json();
            })
            .then(function (data) {
                if (!data.url) throw new Error('no preview');
                urlCache[trackId] = data.url;
                return data.url;
            });
    }

    function play(button) {
        var trackId = button.dataset.trackId;
        if (!trackId) return;

        // Second press on the active button pauses rather than restarting.
        if (currentButton === button && audio && !audio.paused) {
            stopCurrent();
            return;
        }

        stopCurrent();
        currentButton = button;
        setState(button, 'loading');

        fetchPreviewUrl(trackId)
            .then(function (url) {
                var a = getAudio();
                a.src = url;
                return a.play();
            })
            .then(function () {
                if (currentButton !== button) return;   // superseded mid-load
                setState(button, 'playing');
                button.classList.add('is-playing');
            })
            .catch(function () {
                setState(button, 'error');
                currentButton = null;
            });
    }

    // Delegated so cards rendered later (WebSocket results, "show more") work
    // without re-binding.
    document.addEventListener('click', function (e) {
        var button = e.target.closest('.preview-btn');
        if (!button) return;
        e.preventDefault();
        play(button);
    });

    // Keep the audio from outliving the page's visible state on mobile.
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) stopCurrent();
    });

    window.NextTrackPreview = { stop: stopCurrent };
})();
