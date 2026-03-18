/**
 * Recent Searches
 * Saves search history to localStorage for quick re-searching
 */

(function() {
    'use strict';

    const STORAGE_KEY = 'nexttrack_recent_searches';
    const MAX_SEARCHES = 8;  // Keep last 8 searches

    // Get searches from localStorage
    function getRecentSearches() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            return [];
        }
    }

    // Save searches to localStorage
    function saveRecentSearches(searches) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(searches));
        } catch (e) {
            console.warn('[RecentSearches] Storage error:', e);
        }
    }

    // Add a new search to history
    function addSearch(query) {
        if (!query || typeof query !== 'string') return;
        query = query.trim();
        if (query.length < 2) return;

        const searches = getRecentSearches();

        // Remove duplicate if exists (will re-add at top)
        const existingIndex = searches.findIndex(s => s.query.toLowerCase() === query.toLowerCase());
        if (existingIndex > -1) searches.splice(existingIndex, 1);

        // Add to beginning, trim to max
        searches.unshift({ query: query, timestamp: Date.now() });
        while (searches.length > MAX_SEARCHES) searches.pop();

        saveRecentSearches(searches);
        renderRecentSearches();
    }

    // Remove single search from history
    function removeSearch(query) {
        const searches = getRecentSearches();
        const index = searches.findIndex(s => s.query === query);
        if (index > -1) {
            searches.splice(index, 1);
            saveRecentSearches(searches);
            renderRecentSearches();
        }
    }

    // Clear all history
    function clearSearches() {
        saveRecentSearches([]);
        renderRecentSearches();
        if (window.announceToScreenReader) {
            window.announceToScreenReader('Recent searches cleared');
        }
    }

    // Render the recent searches UI
    let isRendering = false;  // Guard against re-entrant calls
    function renderRecentSearches() {
        // Only show on home page
        if (window.location.pathname !== '/' && window.location.pathname !== '') return;
        if (isRendering) return;
        isRendering = true;

        try {
            const searches = getRecentSearches();
            let container = document.getElementById('recent-searches');

            // Create container if needed
            if (!container) {
                container = document.createElement('div');
                container.id = 'recent-searches';
                container.className = 'recent-searches';
                container.setAttribute('role', 'region');
                container.setAttribute('aria-label', 'Recent searches');

                // Insert after search form
                const searchForm = document.querySelector('.search-form, #search-form');
                if (searchForm && searchForm.parentNode) {
                    searchForm.parentNode.insertBefore(container, searchForm.nextSibling);
                }

                addRecentSearchesStyles();
            }

        // Hide if no searches
        if (searches.length === 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';

        // Build HTML
        container.innerHTML = `
            <div class="recent-searches-header">
                <h3><i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i> Recent Searches</h3>
                <button class="recent-searches-clear" onclick="window.clearRecentSearches()" aria-label="Clear all">Clear</button>
            </div>
            <div class="recent-searches-list" role="list">
                ${searches.map(search => `
                    <div class="recent-search-item" role="listitem">
                        <button class="recent-search-query" onclick="window.performRecentSearch('${escapeHtml(search.query)}')" aria-label="Search for ${escapeHtml(search.query)}">
                            <i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i>
                            <span>${escapeHtml(search.query)}</span>
                        </button>
                        <button class="recent-search-remove" onclick="window.removeRecentSearch('${escapeHtml(search.query)}')" aria-label="Remove">
                            <i class="fa-solid fa-xmark" aria-hidden="true"></i>
                        </button>
                    </div>
                `).join('')}
            </div>
        `;
        } finally {
            isRendering = false;
        }
    }

    // Escape HTML to prevent XSS
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML.replace(/'/g, '&#39;').replace(/"/g, '&quot;');
    }

    // Re-run a previous search
    function performSearch(query) {
        const searchInput = document.querySelector('#search-query, #search-input, input[type="search"]');
        const searchForm = document.querySelector('#search-form, .search-form, form[action*="search"]');

        if (searchInput) {
            searchInput.value = query;
            searchInput.focus();
            if (searchForm) searchForm.submit();
            else {
                // Trigger events for AJAX search
                searchInput.dispatchEvent(new Event('input', { bubbles: true }));
                const searchBtn = document.querySelector('button[type="submit"], .search-btn');
                if (searchBtn) searchBtn.click();
            }
        }
    }

    // Inject CSS styles
    function addRecentSearchesStyles() {
        if (document.getElementById('recent-searches-styles')) return;

        const style = document.createElement('style');
        style.id = 'recent-searches-styles';
        style.textContent = `
            .recent-searches {
                margin: 1.5rem 0;
                padding: 1rem;
                background: var(--bg-card, #181818);
                border-radius: 12px;
                border: 1px solid var(--border-color, #282828);
            }
            .recent-searches-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 0.75rem;
            }
            .recent-searches-header h3 {
                margin: 0;
                font-size: 0.85rem;
                font-weight: 600;
                color: var(--text-muted, #b3b3b3);
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }
            .recent-searches-clear {
                background: transparent;
                border: none;
                color: var(--text-muted, #888);
                font-size: 0.75rem;
                cursor: pointer;
                padding: 0.25rem 0.5rem;
                border-radius: 4px;
                transition: all 0.2s;
            }
            .recent-searches-clear:hover {
                color: var(--primary-color, #1DB954);
                background: var(--bg-secondary, #282828);
            }
            .recent-searches-list { display: flex; flex-wrap: wrap; gap: 0.5rem; }
            .recent-search-item {
                display: flex;
                align-items: center;
                background: var(--bg-secondary, #282828);
                border-radius: 20px;
                overflow: hidden;
                transition: all 0.2s;
            }
            .recent-search-item:hover { background: var(--border-color, #333); }
            .recent-search-query {
                display: flex;
                align-items: center;
                gap: 0.4rem;
                padding: 0.4rem 0.5rem 0.4rem 0.75rem;
                background: transparent;
                border: none;
                color: var(--text-color, #fff);
                font-size: 0.85rem;
                cursor: pointer;
            }
            .recent-search-query:hover { color: var(--primary-color, #1DB954); }
            .recent-search-query i { font-size: 0.7rem; color: var(--text-muted, #888); }
            .recent-search-remove {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 24px;
                height: 24px;
                padding: 0;
                margin-right: 0.25rem;
                background: transparent;
                border: none;
                color: var(--text-muted, #666);
                font-size: 0.7rem;
                cursor: pointer;
                border-radius: 50%;
                opacity: 0;
                transition: all 0.2s;
            }
            .recent-search-item:hover .recent-search-remove { opacity: 1; }
            .recent-search-remove:hover { background: rgba(255,255,255,0.1); color: var(--text-color, #fff); }
        `;
        document.head.appendChild(style);
    }

    // Hook into search form submissions to auto-save searches
    function hookSearchForm() {
        const forms = document.querySelectorAll('form');
        forms.forEach(form => {
            const input = form.querySelector('input[type="search"], input[name="q"], #search-query');
            if (input) {
                form.addEventListener('submit', function() {
                    const query = input.value.trim();
                    if (query) addSearch(query);
                });
            }
        });
    }

    // Expose functions globally
    window.addRecentSearch = addSearch;
    window.removeRecentSearch = removeSearch;
    window.clearRecentSearches = clearSearches;
    window.performRecentSearch = performSearch;

    // Register under NextTrack namespace
    window.NextTrack = window.NextTrack || {};
    window.NextTrack.search = window.NextTrack.search || {};
    window.NextTrack.search.recent = {
        add: addSearch,
        remove: removeSearch,
        clear: clearSearches,
        perform: performSearch
    };

    // Initialize
    function init() {
        renderRecentSearches();
        hookSearchForm();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
