/**
 * NextTrack Keyboard Shortcuts
 *
 * Power-user keyboard navigation and shortcuts
 */

(function() {
    'use strict';

    // Shortcut definitions
    const shortcuts = {
        '/': {
            description: 'Focus search',
            action: focusSearch
        },
        's': {
            description: 'Focus search',
            action: focusSearch,
            requireMeta: true
        },
        'p': {
            description: 'Go to playlist',
            action: () => navigateTo('/builder/')
        },
        'r': {
            description: 'Get recommendations',
            action: () => {
                if (window.NextTrackLoadingOverlay) {
                    window.NextTrackLoadingOverlay.navigate();
                } else {
                    navigateTo('/results/');
                }
            }
        },
        'h': {
            description: 'Go home',
            action: () => navigateTo('/')
        },
        'a': {
            description: 'View analytics',
            action: () => navigateTo('/analytics/')
        },
        '?': {
            description: 'Show keyboard shortcuts',
            action: showShortcutsModal
        },
        't': {
            description: 'Start tour',
            action: () => {
                if (window.startTour) window.startTour();
            }
        },
        'Escape': {
            description: 'Close modal/menu',
            action: closeActiveOverlay
        }
    };

    // State
    let shortcutsModalOpen = false;

    // Focus search input
    function focusSearch() {
        const searchInputs = [
            '#search-query',
            '#search-input',
            'input[type="search"]',
            '.search-input',
            'input[placeholder*="Search"]'
        ];

        for (const selector of searchInputs) {
            const input = document.querySelector(selector);
            if (input) {
                input.focus();
                input.select();
                return true;
            }
        }

        // If not on home page, navigate there
        if (window.location.pathname !== '/') {
            navigateTo('/');
        }
        return false;
    }

    // Navigate to URL
    function navigateTo(url) {
        window.location.href = url;
    }

    // Close any active overlay
    function closeActiveOverlay() {
        // Close shortcuts modal
        const shortcutsModal = document.getElementById('shortcuts-modal');
        if (shortcutsModal && shortcutsModal.classList.contains('active')) {
            hideShortcutsModal();
            return true;
        }

        // Close survey modal
        const surveyModal = document.getElementById('survey-modal');
        if (surveyModal && surveyModal.classList.contains('active')) {
            surveyModal.classList.remove('active');
            return true;
        }

        // Close mobile nav
        const navLinks = document.getElementById('nav-links');
        if (navLinks && navLinks.classList.contains('active')) {
            const hamburger = document.getElementById('hamburger');
            if (hamburger) hamburger.click();
            return true;
        }

        // End tour
        if (window.endTour) {
            window.endTour();
            return true;
        }

        return false;
    }

    // Show shortcuts modal
    function showShortcutsModal() {
        let modal = document.getElementById('shortcuts-modal');

        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'shortcuts-modal';
            modal.className = 'shortcuts-modal-overlay';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', 'shortcuts-title');

            const shortcutsList = Object.entries(shortcuts)
                .filter(([key]) => key !== 'Escape')
                .map(([key, { description }]) => `
                    <div class="shortcut-item">
                        <kbd>${formatKey(key)}</kbd>
                        <span>${description}</span>
                    </div>
                `).join('');

            modal.innerHTML = `
                <div class="shortcuts-modal">
                    <div class="shortcuts-header">
                        <h2 id="shortcuts-title">
                            <i class="fa-solid fa-keyboard" aria-hidden="true"></i>
                            Keyboard Shortcuts
                        </h2>
                        <button class="shortcuts-close" onclick="window.hideShortcutsModal()" aria-label="Close">
                            <i class="fa-solid fa-xmark" aria-hidden="true"></i>
                        </button>
                    </div>
                    <div class="shortcuts-body">
                        <div class="shortcuts-section">
                            <h3>Navigation</h3>
                            <div class="shortcut-item"><kbd>H</kbd><span>Go home</span></div>
                            <div class="shortcut-item"><kbd>P</kbd><span>Open playlist</span></div>
                            <div class="shortcut-item"><kbd>R</kbd><span>Get recommendations</span></div>
                            <div class="shortcut-item"><kbd>A</kbd><span>View analytics</span></div>
                        </div>
                        <div class="shortcuts-section">
                            <h3>Recommendations</h3>
                            <div class="shortcut-item"><kbd>J</kbd><span>Next track</span></div>
                            <div class="shortcut-item"><kbd>K</kbd><span>Previous track</span></div>
                            <div class="shortcut-item"><kbd>L</kbd><span>Like focused track</span></div>
                            <div class="shortcut-item"><kbd>D</kbd><span>Dislike focused track</span></div>
                        </div>
                        <div class="shortcuts-section">
                            <h3>Actions</h3>
                            <div class="shortcut-item"><kbd>/</kbd><span>Focus search</span></div>
                            <div class="shortcut-item"><kbd>T</kbd><span>Start tour</span></div>
                            <div class="shortcut-item"><kbd>?</kbd><span>Show this help</span></div>
                            <div class="shortcut-item"><kbd>Esc</kbd><span>Close modal</span></div>
                        </div>
                    </div>
                    <div class="shortcuts-footer">
                        <p>Press <kbd>?</kbd> anytime to show this help</p>
                    </div>
                </div>
            `;

            document.body.appendChild(modal);

            // Close on backdrop click
            modal.addEventListener('click', (e) => {
                if (e.target === modal) hideShortcutsModal();
            });
        }

        modal.classList.add('active');
        shortcutsModalOpen = true;
        document.body.style.overflow = 'hidden';

        // Focus close button
        setTimeout(() => {
            const closeBtn = modal.querySelector('.shortcuts-close');
            if (closeBtn) closeBtn.focus();
        }, 100);
    }

    // Hide shortcuts modal
    window.hideShortcutsModal = function() {
        const modal = document.getElementById('shortcuts-modal');
        if (modal) {
            modal.classList.remove('active');
            shortcutsModalOpen = false;
            document.body.style.overflow = '';
        }
    };

    // Format key for display
    function formatKey(key) {
        const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
        if (key === '/') return '/';
        if (key === '?') return '?';
        if (key === 'Escape') return 'Esc';
        return key.toUpperCase();
    }

    // Check if we should ignore shortcuts (in input, etc.)
    function shouldIgnoreShortcut(e) {
        const target = e.target;
        const tagName = target.tagName.toLowerCase();

        // Ignore if in input, textarea, or contenteditable
        if (tagName === 'input' || tagName === 'textarea' || target.isContentEditable) {
            // Allow / to work as search shortcut even in some inputs
            if (e.key === '/' && tagName === 'input' && target.type === 'search') {
                return false;
            }
            // Allow Escape to work in inputs
            if (e.key === 'Escape') {
                return false;
            }
            return true;
        }

        return false;
    }

    // Main keyboard handler
    function handleKeydown(e) {
        // Check for meta key combinations
        if (e.metaKey || e.ctrlKey) {
            if (e.key.toLowerCase() === 's') {
                e.preventDefault();
                focusSearch();
                return;
            }
            // Don't interfere with other browser shortcuts
            return;
        }

        // Skip if in input
        if (shouldIgnoreShortcut(e)) {
            return;
        }

        // Find matching shortcut
        const shortcut = shortcuts[e.key] || shortcuts[e.key.toLowerCase()];

        if (shortcut && !shortcut.requireMeta) {
            e.preventDefault();
            shortcut.action();
        }
    }

    // Initialize
    document.addEventListener('keydown', handleKeydown);

    // Add visual indicator that shortcuts are available
    document.addEventListener('DOMContentLoaded', function() {
        // Add subtle hint in footer or nav
        const footer = document.querySelector('.footer');
        if (footer) {
            const hint = document.createElement('p');
            hint.className = 'small shortcuts-hint';
            hint.innerHTML = 'Press <kbd>?</kbd> for keyboard shortcuts';
            hint.style.cssText = 'margin-top: 0.5rem; opacity: 0.6;';
            footer.appendChild(hint);
        }
    });

})();
