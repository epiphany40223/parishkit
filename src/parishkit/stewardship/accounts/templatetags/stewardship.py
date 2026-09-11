"""Only typed values reach the canonical display functions; HTML stays escaped."""

from django import template

from parishkit.stewardship.web import presentation

register = template.Library()
for name in ("number", "usd", "percentage", "out_of", "instant", "parish_date"):
    register.filter(name, getattr(presentation, name))


@register.filter
def progress_integer(value):
    """Unsupported component counts select an empty state instead of a render error."""
    if type(value) is not int or value < 0:
        return None
    try:
        presentation.number(value)
    except ValueError:
        return None
    return value
