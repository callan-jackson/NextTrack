"""Custom DRF exception handler for consistent error responses."""

from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """Wrap all DRF error responses in a consistent format."""
    response = exception_handler(exc, context)

    if response is not None:
        detail = response.data.get('detail', response.data) if isinstance(response.data, dict) else response.data
        response.data = {
            'error': {
                'code': response.status_code,
                'message': str(detail) if not isinstance(detail, (dict, list)) else 'Validation error',
                'details': response.data if isinstance(response.data, dict) else None,
            }
        }

    return response
