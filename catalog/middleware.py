"""Custom middleware for request tracing and security."""

import logging
import threading
import uuid

_local = threading.local()

logger = logging.getLogger(__name__)


def get_request_id():
    """Get the current request ID from thread-local storage."""
    return getattr(_local, 'request_id', None)


class RequestIDMiddleware:
    """Assigns a unique ID to each request for end-to-end tracing."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get('HTTP_X_REQUEST_ID') or str(uuid.uuid4())
        _local.request_id = request_id
        request.request_id = request_id

        response = self.get_response(request)
        response['X-Request-ID'] = request_id

        _local.request_id = None
        return response


class RequestIDFilter(logging.Filter):
    """Logging filter that adds request_id to log records."""

    def filter(self, record):
        record.request_id = get_request_id() or '-'
        return True


class ContentSecurityPolicyMiddleware:
    """Add Content-Security-Policy header to all responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        from django.conf import settings

        directives = []
        csp_map = {
            'default-src': getattr(settings, 'CSP_DEFAULT_SRC', ("'self'",)),
            'script-src': getattr(settings, 'CSP_SCRIPT_SRC', ("'self'",)),
            'style-src': getattr(settings, 'CSP_STYLE_SRC', ("'self'",)),
            'font-src': getattr(settings, 'CSP_FONT_SRC', ("'self'",)),
            'img-src': getattr(settings, 'CSP_IMG_SRC', ("'self'",)),
            'connect-src': getattr(settings, 'CSP_CONNECT_SRC', ("'self'",)),
            'frame-src': getattr(settings, 'CSP_FRAME_SRC', ("'self'",)),
        }

        for directive, sources in csp_map.items():
            if sources:
                directives.append(f"{directive} {' '.join(sources)}")

        if directives:
            response['Content-Security-Policy'] = '; '.join(directives)

        # Additional security headers
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        return response
