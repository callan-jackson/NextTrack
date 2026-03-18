/**
 * Playlist Progress Indicator
 * Shows users how many tracks they need for good recommendations
 */

(function() {
    'use strict';

    // Thresholds for recommendation quality
    const MIN_TRACKS = 3;   // Minimum to enable recommendations
    const OPTIMAL_TRACKS = 5;  // Best results at this count

    // Creates the progress bar UI element
    function createProgressIndicator() {
        // Only show on home and builder pages
        const validPages = ['/', '/builder/'];
        if (!validPages.includes(window.location.pathname) && window.location.pathname !== '') return;
        if (document.getElementById('playlist-progress')) return;

        const indicator = document.createElement('div');
        indicator.id = 'playlist-progress';
        indicator.className = 'playlist-progress';
        indicator.setAttribute('role', 'status');
        indicator.setAttribute('aria-live', 'polite');

        indicator.innerHTML = `
            <div class="progress-content">
                <div class="progress-icon">
                    <i class="fa-solid fa-music" aria-hidden="true"></i>
                </div>
                <div class="progress-info">
                    <div class="progress-text">
                        <span class="progress-count">0</span> tracks in playlist
                    </div>
                    <div class="progress-hint"></div>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${OPTIMAL_TRACKS}" aria-valuenow="0">
                        <div class="progress-fill"></div>
                    </div>
                </div>
                <a href="/results/" class="progress-action" onclick="return window.NextTrackLoadingOverlay ? window.NextTrackLoadingOverlay.navigate() : true;">
                    <span class="action-text">Get Recommendations</span>
                    <i class="fa-solid fa-arrow-right" aria-hidden="true"></i>
                </a>
            </div>
        `;

        addProgressStyles();

        // Insert after header
        const header = document.querySelector('header');
        if (header) header.after(indicator);
        else document.body.insertBefore(indicator, document.body.firstChild);

        return indicator;
    }

    // Updates the progress display based on current playlist size
    function updateProgress() {
        const indicator = document.getElementById('playlist-progress');
        if (!indicator) return;

        // Get track count from badge or DOM elements
        let trackCount = 0;
        const badge = document.querySelector('.playlist-count');
        if (badge) trackCount = parseInt(badge.textContent) || 0;

        const playlistItems = document.querySelectorAll('.playlist-item, .track-item[data-in-playlist="true"]');
        if (playlistItems.length > 0) trackCount = Math.max(trackCount, playlistItems.length);

        // Update UI elements
        const countEl = indicator.querySelector('.progress-count');
        const hintEl = indicator.querySelector('.progress-hint');
        const fillEl = indicator.querySelector('.progress-fill');
        const barEl = indicator.querySelector('.progress-bar');
        const actionEl = indicator.querySelector('.progress-action');

        if (countEl) countEl.textContent = trackCount;

        // Progress bar fill percentage (caps at 100%)
        const percentage = Math.min((trackCount / OPTIMAL_TRACKS) * 100, 100);
        if (fillEl) fillEl.style.width = percentage + '%';
        if (barEl) barEl.setAttribute('aria-valuenow', trackCount);

        // Dynamic hint text based on how many tracks user has
        let hintText = '';
        let iconClass = 'fa-music';

        if (trackCount === 0) {
            hintText = 'Add tracks to get personalized recommendations';
            iconClass = 'fa-plus';
            indicator.classList.add('empty');
            indicator.classList.remove('ready', 'optimal');
        } else if (trackCount < MIN_TRACKS) {
            const needed = MIN_TRACKS - trackCount;
            hintText = `Add ${needed} more track${needed > 1 ? 's' : ''} to enable recommendations`;
            iconClass = 'fa-circle-plus';
            indicator.classList.remove('empty', 'ready', 'optimal');
        } else if (trackCount < OPTIMAL_TRACKS) {
            const moreFor = OPTIMAL_TRACKS - trackCount;
            hintText = `Good! Add ${moreFor} more for even better results`;
            iconClass = 'fa-chart-line';
            indicator.classList.add('ready');
            indicator.classList.remove('empty', 'optimal');
        } else {
            hintText = 'Great playlist! Ready for optimal recommendations';
            iconClass = 'fa-check-circle';
            indicator.classList.add('optimal');
            indicator.classList.remove('empty', 'ready');
        }

        if (hintEl) hintEl.textContent = hintText;

        const iconEl = indicator.querySelector('.progress-icon i');
        if (iconEl) iconEl.className = `fa-solid ${iconClass}`;

        // Only enable button when we have enough tracks
        if (actionEl) {
            if (trackCount >= MIN_TRACKS) {
                actionEl.classList.add('enabled');
                actionEl.removeAttribute('aria-disabled');
            } else {
                actionEl.classList.remove('enabled');
                actionEl.setAttribute('aria-disabled', 'true');
            }
        }

        // Hide on results page
        indicator.style.display = window.location.pathname.includes('results') ? 'none' : '';
    }

    // Injects CSS styles for the progress indicator
    function addProgressStyles() {
        if (document.getElementById('playlist-progress-styles')) return;

        const style = document.createElement('style');
        style.id = 'playlist-progress-styles';
        style.textContent = `
            .playlist-progress {
                background: linear-gradient(135deg, var(--bg-card, #181818), var(--bg-secondary, #1a1a1a));
                border-bottom: 1px solid var(--border-color, #282828);
                padding: 0.75rem 1.5rem;
            }
            .progress-content {
                max-width: 1200px;
                margin: 0 auto;
                display: flex;
                align-items: center;
                gap: 1rem;
                flex-wrap: wrap;
            }
            .progress-icon {
                width: 36px;
                height: 36px;
                background: var(--bg-secondary, #282828);
                border-radius: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--text-muted, #b3b3b3);
                font-size: 1rem;
                flex-shrink: 0;
                transition: all 0.3s ease;
            }
            .playlist-progress.ready .progress-icon,
            .playlist-progress.optimal .progress-icon {
                background: var(--primary-color, #1DB954);
                color: var(--secondary-color, #191414);
            }
            .progress-info { flex: 1; min-width: 200px; }
            .progress-text { font-size: 0.9rem; font-weight: 600; color: var(--text-color, #fff); }
            .progress-count { color: var(--primary-color, #1DB954); font-size: 1.1rem; }
            .progress-hint { font-size: 0.8rem; color: var(--text-muted, #b3b3b3); margin-top: 0.15rem; }
            .progress-bar-container { width: 120px; flex-shrink: 0; }
            .progress-bar { height: 6px; background: var(--bg-secondary, #282828); border-radius: 3px; overflow: hidden; }
            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, var(--primary-color, #1DB954), #1ed760);
                border-radius: 3px;
                transition: width 0.4s ease;
                width: 0;
            }
            .progress-action {
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                padding: 0.5rem 1rem;
                background: var(--bg-secondary, #282828);
                color: var(--text-muted, #888);
                border-radius: 50px;
                text-decoration: none;
                font-size: 0.85rem;
                font-weight: 600;
                transition: all 0.2s;
                pointer-events: none;
                opacity: 0.5;
            }
            .progress-action.enabled {
                background: var(--primary-color, #1DB954);
                color: var(--secondary-color, #191414);
                pointer-events: auto;
                opacity: 1;
            }
            .progress-action.enabled:hover { background: #1ed760; transform: translateY(-1px); }
            @media (max-width: 768px) {
                .playlist-progress { padding: 0.75rem 1rem; }
                .progress-content { gap: 0.75rem; }
                .progress-bar-container { width: 100%; order: 10; }
                .progress-action { margin-left: auto; }
                .progress-action .action-text { display: none; }
                .progress-action { padding: 0.5rem; border-radius: 50%; }
            }
        `;
        document.head.appendChild(style);
    }

    // Initialize and watch for changes
    function init() {
        createProgressIndicator();
        updateProgress();

        // Debounced update to prevent infinite loops
        let updateTimeout = null;
        function debouncedUpdate() {
            if (updateTimeout) return;
            updateTimeout = setTimeout(function() {
                updateProgress();
                updateTimeout = null;
            }, 100);
        }

        // Only watch the playlist count badge, not the whole body
        const badge = document.querySelector('.playlist-count');
        if (badge) {
            const observer = new MutationObserver(debouncedUpdate);
            observer.observe(badge, { childList: true, characterData: true, subtree: true });
        }
    }

    // Expose globally so other scripts can trigger updates
    window.updatePlaylistProgress = updateProgress;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
