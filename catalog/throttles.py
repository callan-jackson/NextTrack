"""Custom throttle classes for rate limiting."""

from rest_framework.throttling import SimpleRateThrottle


class SessionFeedbackThrottle(SimpleRateThrottle):
    """Limits feedback submissions to 200/hour per session."""
    rate = '200/hour'

    def get_cache_key(self, request, view):
        if not request.session.session_key:
            request.session.create()
        return f"throttle_feedback_{request.session.session_key}"
