"""Custom pagination classes."""

from rest_framework.pagination import CursorPagination


class CursorPaginationByPopularity(CursorPagination):
    """Cursor-based pagination ordered by popularity for stable infinite scroll."""
    ordering = '-popularity'
    page_size = 20
    cursor_query_param = 'cursor'
