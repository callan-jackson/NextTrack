"""
WebSocket URL Routing Module.

Defines URL patterns for WebSocket connections, analogous to urls.py for HTTP.
Maps WebSocket paths to their corresponding Consumer classes.

URL Patterns:
- ws/search/  ->  SearchConsumer (real-time search with progress)

Security Note:
WebSocket connections bypass Django's CSRF protection by default.
The consumer should implement appropriate authentication/authorization
for sensitive operations in production environments.
"""

from django.urls import re_path

from catalog.consumers import SearchConsumer

websocket_urlpatterns = [
    # Real-time search endpoint
    # Client connects to: ws://localhost:8000/ws/search/
    re_path(r'ws/search/$', SearchConsumer.as_asgi()),
]
