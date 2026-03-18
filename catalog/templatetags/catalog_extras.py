"""
Custom template tags and filters for the catalog app.

Provides utility filters for template rendering, including dictionary
access by key for dynamic data structures like explanations.
"""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary by key in Django templates.

    Usage: {{ mydict|get_item:key_variable }}

    Args:
        dictionary: The dict to access
        key: The key to look up

    Returns:
        The value at dictionary[key], or None if not found
    """
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def percentage(value, max_value=1.0):
    """
    Convert a value to a percentage.

    Usage: {{ value|percentage }} or {{ value|percentage:100 }}

    Args:
        value: The numeric value
        max_value: The maximum value (default 1.0)

    Returns:
        Percentage as integer
    """
    try:
        return int((float(value) / float(max_value)) * 100)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0
