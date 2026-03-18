/**
 * NextTrack Utility Functions
 * Shared helpers used across the application
 */

/**
 * Creates a debounced version of a function that delays invoking func
 * until after `wait` milliseconds have elapsed since the last invocation.
 *
 * @param {Function} func - The function to debounce
 * @param {number} wait - Milliseconds to delay (default: 300)
 * @returns {Function} The debounced function (with .cancel() method)
 */
function debounce(func, wait) {
    if (typeof wait === 'undefined') wait = 300;
    var timeoutId = null;

    function debounced() {
        var context = this;
        var args = arguments;
        if (timeoutId !== null) {
            clearTimeout(timeoutId);
        }
        timeoutId = setTimeout(function() {
            timeoutId = null;
            func.apply(context, args);
        }, wait);
    }

    debounced.cancel = function() {
        if (timeoutId !== null) {
            clearTimeout(timeoutId);
            timeoutId = null;
        }
    };

    return debounced;
}

// Expose globally
window.debounce = debounce;
