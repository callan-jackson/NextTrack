/**
 * NextTrack Common Module
 * Shared functions used across home.html and results.html
 */

/**
 * Get CSRF token from cookie or hidden input
 * @returns {string} The CSRF token value
 */
function getCsrfToken() {
    // Try hidden input first
    var input = document.querySelector('[name=csrfmiddlewaretoken]');
    if (input) {
        return input.value;
    }
    // Fallback: get from cookie
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
        var parts = cookies[i].trim().split('=');
        if (parts[0] === 'csrftoken') {
            return parts[1];
        }
    }
    return '';
}

/**
 * Toast Notification System
 * Provides visual feedback for user actions
 *
 * @param {string} message - HTML message to display
 * @param {string} type - 'success', 'error', or 'warning'
 */
function showToast(message, type) {
    if (typeof type === 'undefined') type = 'success';
    var container = document.getElementById('toast-container');
    if (!container) return;
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;

    var icons = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        warning: 'fa-triangle-exclamation'
    };

    toast.innerHTML =
        '<i class="fa-solid ' + (icons[type] || icons.success) + '"></i>' +
        '<span class="toast-message">' + message + '</span>';

    container.appendChild(toast);

    // Remove toast after animation completes
    setTimeout(function() {
        toast.remove();
    }, 3000);
}

/**
 * Update playlist count badge in navigation
 * @param {number} count - New playlist count
 */
function updatePlaylistCount(count) {
    var countBadge = document.querySelector('.playlist-count');
    if (countBadge) {
        countBadge.textContent = count;
        if (count > 0) {
            countBadge.style.display = '';
        } else {
            countBadge.style.display = 'none';
        }
    }
}

/**
 * Duplicate Confirmation Modal state
 */
var _pendingTrackId = null;
var _pendingButton = null;

/**
 * Show the duplicate song confirmation modal
 *
 * @param {string} trackTitle - Track title
 * @param {string} trackArtist - Track artist name
 * @param {string} trackId - Track ID
 * @param {HTMLElement} button - The button element that triggered the action
 */
var _duplicateFocusTrapCleanup = null;

function showDuplicateModal(trackTitle, trackArtist, trackId, button) {
    _pendingTrackId = trackId;
    _pendingButton = button;
    var nameEl = document.getElementById('modal-track-name');
    if (nameEl) {
        nameEl.textContent = '"' + trackTitle + '" by ' + trackArtist;
    }
    var modal = document.getElementById('duplicate-modal');
    if (modal) {
        modal.classList.add('active');
        _duplicateFocusTrapCleanup = trapFocus(modal);
        var cancelBtn = document.getElementById('modal-cancel');
        if (cancelBtn) cancelBtn.focus();
    }
}

/**
 * Hide the duplicate song confirmation modal
 */
function hideDuplicateModal() {
    var modal = document.getElementById('duplicate-modal');
    if (modal) {
        modal.classList.remove('active');
    }
    if (_duplicateFocusTrapCleanup) {
        _duplicateFocusTrapCleanup();
        _duplicateFocusTrapCleanup = null;
    }
    _pendingTrackId = null;
    _pendingButton = null;
}

/**
 * Add track to playlist via AJAX
 * Handles duplicate confirmation and success notifications
 *
 * @param {string} trackId - Spotify track ID
 * @param {HTMLElement} button - The clicked button element
 * @param {boolean} forceAdd - If true, add even if duplicate
 */
async function addToPlaylist(trackId, button, forceAdd) {
    if (typeof forceAdd === 'undefined') forceAdd = false;

    // Prevent double-clicks
    if (button.classList.contains('adding')) {
        return;
    }

    button.classList.add('adding');
    var originalHtml = button.innerHTML;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Adding...';

    var csrfToken = getCsrfToken();

    try {
        var response = await fetch('/ajax/add-track/', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                track_id: trackId,
                force_add: forceAdd
            })
        });

        if (!response.ok) {
            throw new Error('HTTP ' + response.status);
        }

        var data = await response.json();

        if (data.status === 'added') {
            showToast('Added <strong>"' + data.track_title + '"</strong> by ' + data.track_artist + ' to playlist', 'success');
            button.innerHTML = '<i class="fa-solid fa-check"></i> Added';
            button.classList.add('added');
            button.classList.remove('adding');

            if (window.announceToScreenReader) {
                window.announceToScreenReader('Added ' + data.track_title + ' by ' + data.track_artist + ' to playlist');
            }

            updatePlaylistCount(data.count);

            setTimeout(function() {
                button.innerHTML = originalHtml;
                button.classList.remove('added');
            }, 2000);

        } else if (data.status === 'duplicate') {
            button.innerHTML = originalHtml;
            button.classList.remove('adding');
            showDuplicateModal(data.track_title, data.track_artist, trackId, button);

        } else if (data.status === 'playlist_full') {
            showToast('Playlist is full (maximum 100 tracks)', 'warning');
            button.innerHTML = originalHtml;
            button.classList.remove('adding');

        } else if (data.error) {
            throw new Error(data.error);
        }

    } catch (error) {
        var errorMsg = 'Failed to add track. Please try again.';
        if (error instanceof TypeError) {
            errorMsg = 'You appear to be offline. Check your connection and try again.';
        } else if (error.message && /^HTTP\s5/.test(error.message)) {
            errorMsg = 'Server error - please try again in a moment.';
        } else if (error.message && /^HTTP\s4/.test(error.message)) {
            errorMsg = 'Invalid request. Please refresh the page and try again.';
        }
        showToast(errorMsg, 'error');
        button.innerHTML = originalHtml;
        button.classList.remove('adding');
        if (window.NEXTTRACK_DEBUG) console.error('Add to playlist error:', error);
    }
}

/**
 * Initialize the duplicate modal event listeners.
 * Call this on DOMContentLoaded from pages that have the duplicate-modal markup.
 */
function initDuplicateModal() {
    var duplicateModal = document.getElementById('duplicate-modal');
    var modalCancel = document.getElementById('modal-cancel');
    var modalConfirm = document.getElementById('modal-confirm');

    if (!duplicateModal || !modalCancel || !modalConfirm) return;

    modalCancel.addEventListener('click', hideDuplicateModal);

    duplicateModal.addEventListener('click', function(e) {
        if (e.target === duplicateModal) {
            hideDuplicateModal();
        }
    });

    modalConfirm.addEventListener('click', async function() {
        if (_pendingTrackId && _pendingButton) {
            var trackId = _pendingTrackId;
            var button = _pendingButton;
            hideDuplicateModal();
            await addToPlaylist(trackId, button, true);
        }
    });
}

/**
 * Initialize AJAX interception for inline add-to-playlist forms.
 * Intercepts clicks on .btn-add inside form.inline-form elements.
 */
function initAjaxFormInterceptor() {
    document.addEventListener('click', function(e) {
        var button = e.target.closest('.btn-add');
        if (button && button.closest('form.inline-form')) {
            e.preventDefault();
            var form = button.closest('form');
            var trackIdInput = form.querySelector('[name="track_id"]');
            if (trackIdInput) {
                addToPlaylist(trackIdInput.value, button);
            }
        }
    });
}

/**
 * Focus Trap for Modals
 * Captures Tab/Shift+Tab within a modal's focusable elements.
 *
 * @param {HTMLElement} modalElement - The modal container element
 * @returns {function} cleanup - Call to remove the trap
 */
function trapFocus(modalElement) {
    var focusableSelectors = 'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])';
    var _handler = function(e) {
        if (e.key !== 'Tab') return;
        var focusable = modalElement.querySelectorAll(focusableSelectors);
        if (focusable.length === 0) return;
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (e.shiftKey) {
            if (document.activeElement === first) {
                e.preventDefault();
                last.focus();
            }
        } else {
            if (document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    };
    modalElement.addEventListener('keydown', _handler);

    // Set aria-hidden on main content
    var mainEl = document.querySelector('main');
    if (mainEl) mainEl.setAttribute('aria-hidden', 'true');

    return function() {
        modalElement.removeEventListener('keydown', _handler);
        if (mainEl) mainEl.removeAttribute('aria-hidden');
    };
}

/**
 * Lazy-load Spotify Embeds via IntersectionObserver
 * Swaps data-src to src on iframes when they enter the viewport.
 */
function initLazyEmbeds() {
    var lazyIframes = document.querySelectorAll('iframe[data-src]');
    if (lazyIframes.length === 0) return;

    if ('IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    var iframe = entry.target;
                    iframe.src = iframe.dataset.src;
                    iframe.removeAttribute('data-src');
                    observer.unobserve(iframe);
                }
            });
        }, { rootMargin: '200px' });

        lazyIframes.forEach(function(iframe) {
            observer.observe(iframe);
        });
    } else {
        // Fallback: load all immediately
        lazyIframes.forEach(function(iframe) {
            iframe.src = iframe.dataset.src;
            iframe.removeAttribute('data-src');
        });
    }
}

/**
 * Spotify Embed Failure Handling
 * After 5 seconds, if an iframe hasn't loaded, replace with a fallback card.
 * For lazy-loaded iframes (data-src), the timeout starts when src is set.
 */
function initEmbedFailureHandling() {
    var embeds = document.querySelectorAll('.spotify-embed iframe, .spotify-embed-small iframe');
    embeds.forEach(function(iframe) {
        function startLoadTimeout() {
            var loaded = false;
            iframe.addEventListener('load', function() {
                loaded = true;
            });
            setTimeout(function() {
                if (!loaded && iframe.parentElement) {
                    var trackId = '';
                    var srcAttr = iframe.src || iframe.dataset.src || '';
                    var match = srcAttr.match(/track\/([a-zA-Z0-9]+)/);
                    if (match) trackId = match[1];
                    var title = iframe.getAttribute('title') || 'Track';
                    var fallback = document.createElement('div');
                    fallback.className = 'spotify-embed-fallback';
                    fallback.innerHTML =
                        '<div style="display:flex;align-items:center;gap:0.75rem;padding:1rem;background:var(--bg-card);border-radius:12px;border:1px solid var(--border-color);">' +
                        '<i class="fa-brands fa-spotify" style="font-size:1.5rem;color:#1DB954;"></i>' +
                        '<div style="flex:1;">' +
                        '<div style="font-size:0.9rem;font-weight:600;color:var(--text-color);">' + title.replace('Spotify player for ', '') + '</div>' +
                        '<div style="font-size:0.8rem;color:var(--text-muted);">Embed could not load</div>' +
                        '</div>' +
                        (trackId ? '<a href="https://open.spotify.com/track/' + trackId + '" target="_blank" rel="noopener" class="btn btn-small btn-primary" style="flex-shrink:0;">Open in Spotify</a>' : '') +
                        '</div>';
                    iframe.parentElement.replaceChild(fallback, iframe);
                }
            }, 5000);
        }

        if (iframe.hasAttribute('data-src') && !iframe.src) {
            // For lazy-loaded iframes, watch for src attribute being set
            var srcObserver = new MutationObserver(function(mutations) {
                for (var i = 0; i < mutations.length; i++) {
                    if (mutations[i].attributeName === 'src' && iframe.src) {
                        srcObserver.disconnect();
                        startLoadTimeout();
                        break;
                    }
                }
            });
            srcObserver.observe(iframe, { attributes: true, attributeFilter: ['src'] });
        } else {
            startLoadTimeout();
        }
    });
}

// Expose functions globally (legacy flat names kept for backwards compat)
window.getCsrfToken = getCsrfToken;
window.showToast = showToast;
window.updatePlaylistCount = updatePlaylistCount;
window.showDuplicateModal = showDuplicateModal;
window.hideDuplicateModal = hideDuplicateModal;
window.addToPlaylist = addToPlaylist;
window.initDuplicateModal = initDuplicateModal;
window.initAjaxFormInterceptor = initAjaxFormInterceptor;
window.trapFocus = trapFocus;
window.initLazyEmbeds = initLazyEmbeds;
window.initEmbedFailureHandling = initEmbedFailureHandling;

/**
 * Global Namespace: window.NextTrack
 * Organizes public functions under a single namespace object.
 */
window.NextTrack = window.NextTrack || {};
window.NextTrack.csrf = { getToken: getCsrfToken };
window.NextTrack.toast = { show: showToast };
window.NextTrack.playlist = {
    updateCount: updatePlaylistCount,
    add: addToPlaylist
};
window.NextTrack.modal = {
    showDuplicate: showDuplicateModal,
    hideDuplicate: hideDuplicateModal,
    initDuplicate: initDuplicateModal,
    trapFocus: trapFocus
};
window.NextTrack.forms = { initAjaxInterceptor: initAjaxFormInterceptor };
window.NextTrack.embeds = {
    initLazy: initLazyEmbeds,
    initFailureHandling: initEmbedFailureHandling
};

// Initialize lazy embeds and failure handling on DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        initLazyEmbeds();
        initEmbedFailureHandling();
    });
} else {
    initLazyEmbeds();
    initEmbedFailureHandling();
}
