/**
 * NextTrack Guided Tour
 * Simple spotlight walkthrough for new users
 */

(function() {
    'use strict';

    // Tour steps - home page only, kept simple
    const steps = [
        {
            target: null,
            title: 'Welcome to NextTrack!',
            content: 'Discover your next favorite tracks with AI-powered recommendations. Let me show you around!',
            position: 'center'
        },
        {
            target: '.search-form, #search-form, .search-container',
            title: 'Search for Music',
            content: 'Start by searching for songs or artists you enjoy. We\'ll find them in our database or fetch them from Spotify.',
            position: 'bottom'
        },
        {
            target: '.nav-links a[href="/builder/"]',
            title: 'Build Your Playlist',
            content: 'Add tracks to your playlist here. The more you add, the better your recommendations will be!',
            position: 'bottom'
        },
        {
            target: '.nav-links a[href="/results/"]',
            title: 'Get Recommendations',
            content: 'Once you have some tracks, click here to get personalized AI recommendations based on your taste!',
            position: 'bottom'
        }
    ];

    let currentStep = 0;
    let isActive = false;
    let elements = {};
    let highlightedEl = null;  // Track currently highlighted element

    // Create tour elements
    function createElements() {
        // Backdrop - covers entire screen
        elements.backdrop = document.createElement('div');
        elements.backdrop.id = 'tour-backdrop';
        elements.backdrop.style.cssText = `
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.8); z-index: 99998;
            opacity: 0; transition: opacity 0.3s;
            pointer-events: auto;
        `;
        elements.backdrop.onclick = endTour;

        // Spotlight - highlights target element
        elements.spotlight = document.createElement('div');
        elements.spotlight.id = 'tour-spotlight';
        elements.spotlight.style.cssText = `
            position: absolute; z-index: 99999;
            border-radius: 8px; pointer-events: none;
            box-shadow: 0 0 0 4px #1DB954, 0 0 20px rgba(29,185,84,0.5);
            transition: all 0.3s ease;
        `;

        // Tooltip
        elements.tooltip = document.createElement('div');
        elements.tooltip.id = 'tour-tooltip';
        elements.tooltip.style.cssText = `
            position: absolute; z-index: 100000;
            background: #1a1a1a; border: 1px solid #333;
            border-radius: 12px; padding: 0; max-width: 340px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.5);
            opacity: 0; transition: opacity 0.3s;
        `;

        document.body.appendChild(elements.backdrop);
        document.body.appendChild(elements.spotlight);
        document.body.appendChild(elements.tooltip);
    }

    // Find target element from selector string
    function findTarget(selectorString) {
        if (!selectorString) return null;
        const selectors = selectorString.split(',').map(s => s.trim());
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) return el;
        }
        return null;
    }

    let elements_raised = [];  // Track elements we've modified

    // Clear highlight from previous element and its parents
    function clearHighlight() {
        elements_raised.forEach(item => {
            item.el.style.position = item.origPos;
            item.el.style.zIndex = item.origZ;
        });
        elements_raised = [];
        highlightedEl = null;
    }

    // Position spotlight around element and bring it above backdrop
    function positionSpotlight(el) {
        clearHighlight();

        if (!el) {
            elements.spotlight.style.display = 'none';
            return;
        }

        highlightedEl = el;

        // Raise only the element and its immediate wrapper(s)
        // Don't go beyond nav/form containers to avoid unblurring entire sections
        let current = el;
        let levels = 0;

        while (current && current !== document.body && levels < 3) {
            // Stop BEFORE major layout containers
            if (['MAIN', 'SECTION', 'ARTICLE'].includes(current.tagName)) break;

            const style = window.getComputedStyle(current);
            elements_raised.push({
                el: current,
                origPos: current.style.position,
                origZ: current.style.zIndex
            });
            if (style.position === 'static') {
                current.style.position = 'relative';
            }
            current.style.zIndex = '99999';

            // For header elements, include header then stop
            if (current.tagName === 'HEADER') break;

            current = current.parentElement;
            levels++;
        }

        const rect = el.getBoundingClientRect();
        const pad = 6;
        elements.spotlight.style.display = 'block';
        elements.spotlight.style.top = (rect.top + window.scrollY - pad) + 'px';
        elements.spotlight.style.left = (rect.left - pad) + 'px';
        elements.spotlight.style.width = (rect.width + pad * 2) + 'px';
        elements.spotlight.style.height = (rect.height + pad * 2) + 'px';
    }

    // Show step
    function showStep() {
        const step = steps[currentStep];
        const target = findTarget(step.target);

        // Scroll target into view first
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        setTimeout(() => {
            positionSpotlight(target);

            // Build tooltip HTML
            const isLast = currentStep === steps.length - 1;
            const isFirst = currentStep === 0;

            elements.tooltip.innerHTML = `
                <div style="padding: 1rem 1.25rem; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 0.75rem;">
                    <div style="width: 36px; height: 36px; background: #1DB954; border-radius: 8px; display: flex; align-items: center; justify-content: center;">
                        <i class="fa-solid fa-music" style="color: #000;"></i>
                    </div>
                    <h3 style="margin: 0; font-size: 1rem; flex: 1;">${step.title}</h3>
                    <button onclick="window.endTour()" style="background: none; border: none; color: #888; cursor: pointer; padding: 4px;">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
                <div style="padding: 1rem 1.25rem;">
                    <p style="margin: 0; color: #aaa; line-height: 1.5;">${step.content}</p>
                </div>
                <div style="padding: 0.75rem 1.25rem; background: #111; border-top: 1px solid #333; display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: #666; font-size: 0.8rem;">${currentStep + 1} of ${steps.length}</span>
                    <div style="display: flex; gap: 0.5rem;">
                        ${!isFirst ? '<button onclick="window.prevTourStep()" style="background: #333; border: none; color: #fff; padding: 0.5rem 1rem; border-radius: 20px; cursor: pointer;">Back</button>' : ''}
                        <button onclick="${isLast ? 'window.endTour()' : 'window.nextTourStep()'}" style="background: #1DB954; border: none; color: #000; padding: 0.5rem 1rem; border-radius: 20px; cursor: pointer; font-weight: 600;">
                            ${isLast ? 'Done' : 'Next'}
                        </button>
                    </div>
                </div>
            `;

            // Position tooltip
            if (!target || step.position === 'center') {
                elements.tooltip.style.top = '50%';
                elements.tooltip.style.left = '50%';
                elements.tooltip.style.transform = 'translate(-50%, -50%)';
            } else {
                const rect = target.getBoundingClientRect();
                elements.tooltip.style.transform = '';

                // Position below target by default
                let top = rect.bottom + window.scrollY + 16;
                let left = rect.left + (rect.width / 2) - 170;

                // Keep in viewport
                left = Math.max(16, Math.min(left, window.innerWidth - 356));

                elements.tooltip.style.top = top + 'px';
                elements.tooltip.style.left = left + 'px';
            }

            // Make sure tooltip is visible
            elements.tooltip.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }, 200);
    }

    // Start tour
    function startTour() {
        if (isActive) return;

        // Only run on home page
        if (window.location.pathname !== '/' && window.location.pathname !== '') {
            window.location.href = '/?tour=1';
            return;
        }

        isActive = true;
        currentStep = 0;

        if (!elements.backdrop) createElements();

        elements.backdrop.style.opacity = '1';
        elements.tooltip.style.opacity = '1';

        showStep();

        document.addEventListener('keydown', handleKeys);
    }

    // End tour
    function endTour() {
        if (!isActive) return;
        isActive = false;

        clearHighlight();
        elements.backdrop.style.opacity = '0';
        elements.tooltip.style.opacity = '0';
        elements.spotlight.style.display = 'none';

        setTimeout(() => {
            if (elements.backdrop) elements.backdrop.style.pointerEvents = 'none';
        }, 300);

        document.removeEventListener('keydown', handleKeys);
        localStorage.setItem('nexttrack_tour_done', 'true');
    }

    // Next/prev
    function nextStep() {
        if (currentStep < steps.length - 1) {
            currentStep++;
            showStep();
        } else {
            endTour();
        }
    }

    function prevStep() {
        if (currentStep > 0) {
            currentStep--;
            showStep();
        }
    }

    // Keyboard nav
    function handleKeys(e) {
        if (e.key === 'Escape') endTour();
        if (e.key === 'ArrowRight' || e.key === 'Enter') nextStep();
        if (e.key === 'ArrowLeft') prevStep();
    }

    // Expose globally
    window.startTour = startTour;
    window.endTour = endTour;
    window.nextTourStep = nextStep;
    window.prevTourStep = prevStep;

    // Register under NextTrack namespace
    window.NextTrack = window.NextTrack || {};
    window.NextTrack.tour = {
        start: startTour,
        end: endTour,
        next: nextStep,
        prev: prevStep
    };

    // Add floating help button and check for auto-start
    document.addEventListener('DOMContentLoaded', function() {
        // Create floating ? button
        const helpBtn = document.createElement('button');
        helpBtn.id = 'tour-help-btn';
        helpBtn.innerHTML = '<i class="fa-solid fa-question"></i>';
        helpBtn.title = 'Take a Tour';
        helpBtn.onclick = startTour;
        helpBtn.style.cssText = `
            position: fixed; bottom: 1.5rem; right: 1.5rem;
            width: 50px; height: 50px; border-radius: 50%;
            background: #1DB954; color: #000; border: none;
            font-size: 1.25rem; cursor: pointer; z-index: 9999;
            box-shadow: 0 4px 20px rgba(29,185,84,0.4);
            display: flex; align-items: center; justify-content: center;
            transition: transform 0.2s, box-shadow 0.2s;
        `;
        helpBtn.onmouseenter = function() {
            this.style.transform = 'scale(1.1)';
            this.style.boxShadow = '0 6px 30px rgba(29,185,84,0.5)';
        };
        helpBtn.onmouseleave = function() {
            this.style.transform = 'scale(1)';
            this.style.boxShadow = '0 4px 20px rgba(29,185,84,0.4)';
        };
        document.body.appendChild(helpBtn);

        // Auto-start if URL has tour param
        if (window.location.search.includes('tour=1')) {
            setTimeout(startTour, 500);
            history.replaceState({}, '', window.location.pathname);
        }
    });

})();
