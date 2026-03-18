"""Context processors for the catalog app."""


def playlist_context(request):
    """Add playlist count to template context for navbar badge."""
    playlist = request.session.get('playlist', [])
    return {
        'playlist_count': len(playlist),
    }
